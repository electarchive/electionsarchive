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

SOURCE = "us.bloomington_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Bloomington City Clerk"
FILES = "https://www.bloomingtonmn.gov/sites/default/files/"
CONTESTS = {
    "2021-11-02:at-large": (
        FILES + "2021-11/City%20Council%20Member%20At-Large%20Final%20Tabulation%20Results.pdf",
        "City Council Member At-Large, final tabulation results",
        None,
    ),
    "2021-11-02:district-4": (
        FILES + "2021-11/City%20Council%20Member%20District%204%20Final%20Tabulation%20Results.pdf",
        "City Council Member District 4, final tabulation results",
        "4",
    ),
    "2025-11-04:district-1": (
        FILES + "2025-11/2025%20Official%20General%20Election%20Results.pdf",
        "2025 official general election results",
        "1",
    ),
}
METHOD = "This data is transcribed from the city clerk's ranked-choice tabulation statement. Bloomington eliminates every candidate except the top two after round 1, so the count has two rounds. Individual write-in names are combined into one write-in line. Round 1 counts each ballot's highest valid ranking; this may differ slightly from the first-choice totals in the precinct results. The 2021 statements are stamped unofficial until canvassed, but they match the canvassed winners."


def fetch() -> None:
    for url, _, _ in CONTESTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {}
    for key, (url, _, _) in CONTESTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
        ).stdout
        if key.startswith("2025"):
            if text.count("Tabulation Center Summary Statement") != 1 or "District I\n" not in text:
                raise ValueError(f"{key}: expected one tabulation, for District I")
            text = text[text.index("Tabulation Center Summary Statement") :]
        text = text[text.index("Candidate") : text.index("Threshold")]
        named: dict[str, list[int]] = {}
        totals: dict[str, list[int]] = {}
        for line in text.splitlines():
            match = re.fullmatch(r"(\S.*?)\s{2,}(-?[\d,]+)((?:\s+-?[\d,]+)*)(?:\s+[\d.]+%)?\s*", line)
            if match is None or match[1].startswith(("Candidate", "Write", "Undeclared")):
                continue
            numbers = [int(item.replace(",", "")) for item in [match[2], *match[3].split()]]
            label = match[1].replace("‐", "-")
            if label.casefold() in ("inactive ballots", "inactive", "total ballots", "total", "active ballots"):
                totals[label.casefold().split()[0]] = numbers
            else:
                named[label] = numbers
        for label, numbers in [*named.items(), ("inactive", totals["inactive"])]:
            if len(numbers) != 3 or numbers[0] + numbers[1] != numbers[2]:
                raise ValueError(f"{key}: {label} {numbers}")
        total = totals["total"][0]
        active = [total - totals["inactive"][0], total - totals["inactive"][2]]
        if "active" in totals and totals["active"] != active:
            raise ValueError(f"{key}: active ballots {totals['active']} against {active}")
        rounds = [
            {label: numbers[0] for label, numbers in named.items()},
            {label: numbers[2] for label, numbers in named.items()},
        ]
        for number, tally in enumerate(rounds):
            tally["Write-in"] = active[number] - sum(tally.values())
            if tally["Write-in"] < 0:
                raise ValueError(f"{key}: round {number + 1} write-ins")
        if len([label for label, votes in rounds[1].items() if votes and label != "Write-in"]) != 2:
            raise ValueError(f"{key}: round 2 does not keep two candidates")
        if 2 * max(rounds[1].values()) <= active[1]:
            raise ValueError(f"{key}: no majority in round 2")
        contests[key] = {"ballots": total, "invalid": totals["inactive"][0], "rounds": rounds}
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Bloomington, Minnesota's ranked-choice council races that went past round 1: the 2021 at-large and "
            "District 4 seats and 2025 District I, from the city clerk's tabulation statements",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "2706616")
        council = cs.key("us.body", "MN:2706616:council")
        if {city, council} & cs.minted:
            raise ValueError("Bloomington and its council must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        irv = cs.key("us.electoral_system", "bloomington-mn:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv", "batchElimination": True, "maxRankings": 3},
            rules_version=1,
        )
        name(irv, "Bloomington ranked-choice voting")
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
            url, title, district = CONTESTS[key]
            ((parent,),) = conn.execute(
                """select e.id from election e join name n on n.entity_id = e.id
                   where e.date_from = %s and e.parent_id is null and n.text = %s""",
                [day, f"{day.year} Minnesota local elections"],
            ).fetchall()
            election = cs.key("us.election", f"{day}:MN:2706616")
            if election in cs.minted and election not in done:
                done.add(election)
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
                name(election, f"{day.year} Bloomington local election")
                cs.add("slug", path=f"us/mn/bloomington/{day}-local", entity_id=election)
            area, top = city, cs.unit("us.place", "2706616")
            if district is not None:
                code = f"27:2706616:council:district-{district}"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                if {area, top} & cs.minted:
                    raise ValueError(f"{code} must already exist")
            seat = "At-Large" if district is None else f"District {district}"
            contest = cs.key("us.contest", f"{day}:MN:2706616:council:{slug(seat)}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                office_id=None,
                body_id=council,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=irv,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"{day.year} Bloomington City Council election, {seat}")
            cs.add("slug", path=f"us/mn/bloomington/{day}-local/council-{slug(seat)}", entity_id=contest)
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
            source = cs.source(PUBLISHER, f"{day.year} {title}")
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=f"{day.year} {title}",
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), source)
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
            valid = sum(rounds[0].values())
            values = [(None, 3, 0, record["ballots"]), (None, 5, 0, record["invalid"]), (None, 4, 1, valid)]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
                values.extend(
                    (options[label], 1, number, votes)
                    for label, votes in tally.items()
                    if number == 1 or rounds[0][label] or votes
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
