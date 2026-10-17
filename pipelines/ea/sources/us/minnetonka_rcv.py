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

SOURCE = "us.minnetonka_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Minnetonka"
DOCUMENT = "https://web.archive.org/web/{}id_/https://www.minnetonkamn.gov/home/showpublisheddocument/{}"
STATEMENTS = {
    "2021-11-02:Mayor": ("20230722154245", "9898/637721487332700000"),
    "2021-11-02:At-Large A": ("20230722165912", "9900/637721487600670000"),
    "2021-11-02:At-Large B": ("20240722113657", "9902/638348597699270000"),
    "2023-11-07:Ward 1": ("20240722163704", "13615/638351234940370000"),
    "2023-11-07:Ward 2": ("20240722084024", "13617/638351234947070000"),
    "2023-11-07:Ward 3": ("20240722105434", "13619/638351234955830000"),
    "2023-11-07:Ward 4": ("20240722175150", "13621/638351234965370000"),
    "2025-11-04:Mayor": ("20260421233117", "15189/638979452676200000"),
    "2025-11-04:At-Large A": ("20260421230454", "15191/638979452892030000"),
    "2025-11-04:At-Large B": ("20260423040943", "15193/638979453081900000"),
}
RECOUNT = ("20240722211052", "13691/638360672801500000")
METHOD = "Transcribed from the city's ranked-choice tabulation summary statement, as archived by the Internet Archive. In races decided in round 1, overvotes and undervotes are counted here as invalid. In races that went to later rounds, the statement leaves out blank and defective ballots, which are counted here as invalid. Undeclared write-ins are recorded on one write-in line."
MERGED = "This record combines the 2021 at-large seat A and seat B races into one contest. The city's results, which treat each seat as its own contest, are the default."
CAVEAT = "A hand recount on Nov. 17, 2023, found final-round totals of 1,365 for Paula Ramaley and 1,361 for David Haeg, with 635 exhausted ballots; the city lists those as the official vote totals. The rounds shown here are from the original tabulation, which had 1,366 to 1,362. Ramaley won both counts."


def fetch() -> None:
    for stamp, path in [*STATEMENTS.values(), RECOUNT]:
        raw.fetch(SOURCE, DOCUMENT.format(stamp, path), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {}
    for key, (stamp, path) in STATEMENTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, DOCUMENT.format(stamp, path)).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        day = date.fromisoformat(key[:10])
        heading = re.search(r"(\w+ \d+, \d{4}) Municipal Election\s+(.+)", text)
        if heading is None or heading[1] != f"{day:%B} {day.day}, {day.year}":
            raise ValueError(f"{key}: statement is for another election")
        title = heading[2].strip()
        seat = re.sub(
            r"^(?:At-Large Councilmember, Seat ([AB])|Ward (\d) Councilmember)$",
            lambda found: f"At-Large {found[1]}" if found[1] else f"Ward {found[2]}",
            title,
        )
        if seat != key[11:]:
            raise ValueError(f"{key}: statement is for {title}")
        header = {
            label.casefold(): int(re.sub(r"[,.]", "", value))
            for label, value in re.findall(
                r"(Total Votes Cast|Total Ballots Cast|Undervotes|Blank|Defective Ballots|Threshold):\s+([\d,.]+)", text
            )
        }
        rows: dict[str, list[int]] = {}
        body = text[text.index("\nCandidate") if "\nCandidate" in text else text.index("Candidate") :]
        for line in body.splitlines():
            match = re.fullmatch(r"(\S.*?)\s{2,}(-?[\d,]+(?:\s+-?[\d,]+)*)\s+([\d.]+)%?", line.strip())
            if match is not None and not match[1].startswith("Candidate"):
                rows[match[1].strip()] = [int(item.replace(",", "")) for item in match[2].split()]
        total = rows.pop("TOTAL:")
        width = len(next(iter(rows.values())))
        if any(len(numbers) != width for numbers in rows.values()) or len(total) != width // 2 + 1:
            raise ValueError(f"{key}: ragged rows")
        rounds: list[dict[str, int]] = []
        exhausted: list[int] = []
        over = 0
        for number in range(0, width, 2):
            tally: dict[str, int] = {}
            for label, numbers in rows.items():
                if number and numbers[number - 2] + numbers[number - 1] != numbers[number]:
                    raise ValueError(f"{key}: {label} transfer does not add up")
                if label.startswith("Exhausted"):
                    exhausted.append(numbers[number])
                elif label.startswith("Undeclared"):
                    tally["Write-in"] = numbers[number]
                elif label in ("Over-Votes", "Overvotes", "Undervotes"):
                    over += numbers[number]
                else:
                    tally[label] = numbers[number]
            if sum(tally.values()) + over + (exhausted[-1] if exhausted else 0) != total[number // 2]:
                raise ValueError(f"{key}: round {len(rounds) + 1} does not sum to the total")
            rounds.append(tally)
        if exhausted:
            ballots = total[0] + header["blank" if "blank" in header else "undervotes"] + header["defective ballots"]
            invalid = ballots - total[0]
            if header["total votes cast"] != total[0] or header["threshold"] != total[0] // 2 + 1:
                raise ValueError(f"{key}: header disagrees with the table")
        else:
            ballots, invalid = total[0], over
            if header.get("total ballots cast", header.get("total votes cast")) != ballots:
                raise ValueError(f"{key}: header disagrees with the table")
        if 2 * max(rounds[-1].values()) <= ballots - invalid and not exhausted:
            raise ValueError(f"{key}: single round without a majority")
        for earlier, later in itertools.pairwise(rounds):
            if any(later[label] < votes for label, votes in earlier.items() if later[label]):
                raise ValueError(f"{key}: a continuing candidate lost votes")
        contests[key] = {"stamp": stamp, "path": path, "ballots": ballots, "invalid": invalid, "rounds": rounds}
    recount = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, DOCUMENT.format(*RECOUNT)).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if "Recount Round Results Summary" not in recount or "Ward 3 Councilmember" not in recount:
        raise ValueError("recount summary is for another contest")
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnetonka, Minnesota's ranked-choice mayor and council elections, 2021 to 2025, from the city's "
            "tabulation summary statements, with the 2021 contests restated as IRV and the 2021 at-large seats split "
            "into A and B",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "2743252")
        council = cs.key("us.body", "MN:2743252:council")
        mayor = cs.key("us.office", "MN:2743252:mayor")
        if {city, council, mayor} & cs.minted:
            raise ValueError("Minnetonka, its council, and its mayor must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        irv = cs.key("us.electoral_system", "minnetonka:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv"},
            rules_version=1,
        )
        name(irv, "Minnetonka ranked-choice voting")

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
        for (contest,) in conn.execute(
            """select k.id from contest k join election e on e.id = k.election_id
               where e.polity_id = %s and e.date_from = '2021-11-02' and k.kind in ('office', 'body_seats')""",
            [polity],
        ).fetchall():
            (row,) = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchall()
            cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=irv)

        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add(person)

        fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness")
        fields += ("supersedes_id",)
        done: set[object] = set()
        for key, record in contests.items():
            day = date.fromisoformat(key[:10])
            seat = key[11:]
            existing = conn.execute(
                "select id from election where polity_id = %s and date_from = %s and parent_id is not null",
                [polity, day],
            ).fetchall()
            if existing:
                ((election,),) = existing
            else:
                election = cs.key("us.election", f"{day}:MN:2743252")
                if election not in done:
                    done.add(election)
                    ((parent,),) = conn.execute(
                        """select e.id from election e join name n on n.entity_id = e.id
                           where e.date_from = %s and e.parent_id is null and n.text = %s""",
                        [day, f"{day.year} Minnesota local elections"],
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
                    name(election, f"{day.year} Minnetonka local election")
                    cs.add("slug", path=f"us/mn/minnetonka/{day}-local", entity_id=election)
            area, top = city, cs.unit("us.place", "2743252")
            if seat.startswith("Ward"):
                code = f"27:2743252:council:district-{seat[-1]}"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                if {area, top} & cs.minted:
                    raise ValueError(f"{code} must already exist")
            heading = (
                f"{day.year} Minnetonka mayoral election"
                if seat == "Mayor"
                else f"{day.year} Minnetonka City Council election, {seat.replace('Ward', 'District')}"
            )
            found = conn.execute(
                "select k.id from contest k join name n on n.entity_id = k.id where n.text = %s and k.election_id = %s",
                [heading, election],
            ).fetchall()
            if seat.startswith("At-Large") and day.year == 2021:
                ((merged,),) = conn.execute(
                    "select k.id from contest k join name n on n.entity_id = k.id where n.text = %s",
                    ["2021 Minnetonka City Council election, At-Large"],
                ).fetchall()
                if merged not in done:
                    done.add(merged)
                    for values in conn.execute(
                        f"select {', '.join(fields)} from result_set where contest_id = %s and is_default", [merged]
                    ).fetchall():
                        cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
                        cs.add("note", entity_id=values[0], kind="caveat", body_md=MERGED)
            if found:
                ((contest,),) = found
                for values in conn.execute(
                    f"""select {", ".join(fields)} from result_set where contest_id = %s and is_default
                        and changeset_id not in (select id from changeset where pipeline = %s)""",
                    [contest, SOURCE],
                ).fetchall():
                    cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
            else:
                part = "mayor" if seat == "Mayor" else "council-" + slug(seat.replace("Ward", "District"))
                contest = cs.key("us.contest", f"{day}:MN:2743252:{part}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office" if seat == "Mayor" else "body_seats",
                    office_id=mayor if seat == "Mayor" else None,
                    body_id=None if seat == "Mayor" else council,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=irv,
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, heading)
                cs.add("slug", path=f"us/mn/minnetonka/{day}-local/{part}", entity_id=contest)
            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            rounds = record["rounds"]
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                text = label.replace(" (Declared Write-In)", "")
                if label == "Write-in":
                    options[label] = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=options[label], contest_id=contest, kind="write_in", ballot_label=label)
                    continue
                words = spell(text)
                if (words[0], words[-1]) in current:
                    options[label] = current[words[0], words[-1]]
                    continue
                first = {"deborah": "deb", "ash": "ashwin"}.get(words[0], words[0])
                matches = people.get((first, words[-1]), set())
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]}"))
                    if person not in done:
                        done.add(person)
                        cs.add("person", id=person)
                        name(person, text)
                    people[words[0], words[-1]] = {person}
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=text)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=text,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")
            url = DOCUMENT.format(record["stamp"], record["path"])
            title = f"{day.year} {seat} ranked-choice voting tabulation summary statement"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url.split("id_/", 1)[1],
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), source)
            if key == "2023-11-07:Ward 3":
                cs.artifact(raw.cached(SOURCE, DOCUMENT.format(*RECOUNT)), source)
            result = cs.key(f"{SOURCE}.result_set", key)
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
            if key == "2023-11-07:Ward 3":
                cs.add("note", entity_id=result, kind="caveat", body_md=CAVEAT)
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
