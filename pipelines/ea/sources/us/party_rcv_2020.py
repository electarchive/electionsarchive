import json
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.party_rcv_2020"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
PRIMARIES = {
    "HI": (
        date(2020, 5, 22),
        "Hawaii",
        "15",
        "Democratic Party of Hawaiʻi",
        "Hawaiʻi Presidential Democratic Party-Run Primary, Statement of Vote, May 23, 2020",
        "https://hawaiidemocrats.org/wp-content/uploads/2020/05/HawaiiDemocraticPartySOVFinalRevF.pdf",
        "20200607034509",
        {"First Congressional District": "1501", "Second Congressional District": "1502"},
    ),
}
METHOD = {
    "HI": "Data from the Statement of Vote for the Democratic Party of Hawaiʻi's party-run, mail-in presidential primary, prepared by election administrator Merriman River Group. Each congressional district was tabulated separately using ranked-choice voting; the last-place candidate was eliminated each round until every remaining candidate had at least 15 percent. Statewide totals are the sums of the two districts. Void ballots are counted as invalid.",
}


def fetch() -> None:
    for _, _, _, _, _, url, stamp, _ in PRIMARIES.values():
        raw.fetch(SOURCE, WAYBACK.format(stamp, url), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    primaries = {}
    for state, (_, _, _, _, _, url, stamp, codes) in PRIMARIES.items():
        text = subprocess.run(
            [
                "pdftotext",
                "-layout",
                str(raw.cached(SOURCE, WAYBACK.format(stamp, url)).path),
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        pages = text.split("\f")
        districts: dict[str, tuple[int, int, dict[str, list[int]]]] = {}
        for district in codes:
            rounds: dict[str, list[int]] = {}
            totals: list[int] = []
            void: list[int] = []
            final: dict[str, int] = {}
            received = 0
            for page in pages:
                if district not in page:
                    continue
                match = re.search(r"Ballots received: ([\d,]+)", page)
                if match is None:
                    raise ValueError(f"{state} {district}: a page without ballots received")
                received = int(match[1].replace(",", ""))
                if "Final Results" in page:
                    for label, votes in re.findall(r"^(\S.*?)\s{2,}(\d+)", page, re.MULTILINE):
                        final[label.strip()] = int(votes)
                    continue
                if "Ranked Choice Voting" not in page:
                    continue
                for line in page.splitlines():
                    if "Round " in line:
                        continue
                    numbers = [int(cell) for cell in re.findall(r"`?(\d+)(?:\s*\(X\))?", line)]
                    label = re.match(r"\s*([A-Za-z][A-Za-z. ]*[A-Za-z.])\s{2,}", line)
                    if len(numbers) != 5:
                        continue
                    if label is None:
                        totals.extend(numbers)
                    elif label[1] == "VOID":
                        void.extend(numbers)
                    else:
                        rounds.setdefault(label[1], []).extend(numbers)
            for number, total in enumerate(totals):
                if sum(values[number] for values in rounds.values()) != total:
                    raise ValueError(f"{state} {district} round {number + 1}: the rows do not add up")
                if total + void[number] != received:
                    raise ValueError(f"{state} {district} round {number + 1}: the ballots do not add up")
            for label, values in rounds.items():
                printed = final["Exhausted/Other" if label == "Exhausted" else label]
                if values[-1] != printed:
                    raise ValueError(f"{state} {district}: {label}'s final round differs from the final results")
            if final["TOTAL"] != received or final["Void before tabulation"] != void[0]:
                raise ValueError(f"{state} {district}: the final results do not add up")
            districts[district] = (received, void[0], rounds)
        summary = pages[0]
        statewide = {
            label: int(votes.replace(",", ""))
            for label, votes in re.findall(r"^(\S[^:\n]*):\s+([\d,]+)", summary, re.MULTILINE)
        }
        for label, index in (("Ballots received", 0), ("Voided Ballots", 1)):
            if statewide[label] != sum(record[index] for record in districts.values()):
                raise ValueError(f"{state}: {label} differs from the districts' sum")
        for label in next(iter(districts.values()))[2]:
            printed = statewide.get("Exhausted Ballots (Other)" if label == "Exhausted" else label)
            if printed is not None and printed != sum(record[2][label][-1] for record in districts.values()):
                raise ValueError(f"{state}: {label}'s statewide final differs from the districts' sum")
        primaries[state] = {
            district: {"received": received, "void": void, "rounds": rounds}
            for district, (received, void, rounds) in districts.items()
        }
    (PARSED / "primaries.json").write_text(json.dumps(primaries))


def emit() -> Path:
    primaries = json.loads((PARSED / "primaries.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2020 Democratic party-run ranked-choice presidential primaries, every round by congressional "
            "district from the parties' official statements of vote",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        democrats = cs.key("us.party", "democratic-party")
        president = cs.key("us.office", "president")
        nomination = cs.key("us.election", "2020-02-03:democratic-presidential-primaries")
        national = cs.key("us.contest", "2020-02-03:president:democratic")
        if cs.minted & {democrats, president, nomination, national}:
            raise ValueError("the Democratic Party, the presidency, and the 2020 nomination must already exist")
        people: dict[str, set[UUID]] = {}
        for text, person in conn.execute(
            """select c.ballot_name, c.person_id from candidacy c join contest_option o on o.id = c.option_id
               join contest k on k.id = o.contest_id where k.allocation_parent_id = %s""",
            [national],
        ).fetchall():
            people.setdefault(text, set()).add(person)

        for state, (day, title, fips, publisher, heading, url, stamp, codes) in PRIMARIES.items():
            districts = primaries[state]
            polity = cs.polity(f"US-{state}")
            whole = cs.unit("us.state", fips)
            area = cs.area("us.state", fips)
            units = {district: cs.unit("us.cd", code) for district, code in codes.items()}
            if cs.minted & {whole, area, *units.values()}:
                raise ValueError(f"{state}: the state and its congressional districts must already exist")
            source = cs.source(publisher, heading)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=heading,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, url)), source)
            rules = cs.key("us.electoral_system", f"{state}:democratic-party-run-primary")
            cs.add("electoral_system", id=rules, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
            name(rules, f"{title} Democratic party-run primary ranked-choice voting")
            election = cs.key("us.election", f"{day}:{state}:democratic-party-run-primary")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=nomination,
                kind="primary",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"2020 {title} Democratic party-run presidential primary")
            cs.add("slug", path=f"us/{state.lower()}/{day}-democratic-party-run-primary", entity_id=election)
            contest = cs.key("us.contest", f"{day}:{state}:president:democratic")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="delegate_allocation",
                office_id=president,
                party_scope_id=democrats,
                district_area_id=area,
                round_number=1,
                allocation_parent_id=national,
                electoral_system_id=rules,
                default_result_kind="counted",
            )
            name(contest, f"2020 {title} Democratic presidential primary")
            cs.add("slug", path=f"us/{state.lower()}/{day}-democratic-party-run-primary/president", entity_id=contest)
            result = cs.key(f"{SOURCE}.result_set", f"{day}:{state}")
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
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD[state])

            options: dict[str, UUID] = {}
            labels = [label for label in next(iter(districts.values()))["rounds"] if label != "Exhausted"]
            for label in labels:
                if label == "Uncommitted":
                    options[label] = cs.key("us.option", f"{contest}:uncommitted")
                    cs.add(
                        "contest_option", id=options[label], contest_id=contest, kind="uncommitted", ballot_label=label
                    )
                    continue
                (person,) = people[label]
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=democrats,
                    is_independent=False,
                    is_incumbent=None,
                )

            values: list[tuple[UUID, UUID | None, int, int, int, int]] = []
            last = len(next(iter(districts.values()))["rounds"]["Exhausted"])
            for district, record in districts.items():
                unit = units[district]
                rounds = record["rounds"]
                valid = record["received"] - record["void"]
                values += [
                    (unit, None, 3, 0, record["received"], 0),
                    (unit, None, 5, 0, record["void"], 0),
                    (unit, None, 4, 1, valid, 1),
                ]
                values += [(unit, None, 21, number, votes, 0) for number, votes in enumerate(rounds["Exhausted"], 1)]
                for label in labels:
                    cells = rounds[label]
                    for number, votes in enumerate(cells, 1):
                        if number == 1 or cells[number - 2]:
                            values.append((unit, options[label], 1, number, votes, 0))
            received = sum(record["received"] for record in districts.values())
            void = sum(record["void"] for record in districts.values())
            values += [
                (whole, None, 3, 0, received, 0),
                (whole, None, 5, 0, void, 0),
                (whole, None, 4, 1, received - void, 1),
                (whole, None, 21, last, sum(record["rounds"]["Exhausted"][-1] for record in districts.values()), 0),
            ]
            final: dict[str, int] = {}
            for label in labels:
                first = sum(record["rounds"][label][0] for record in districts.values())
                final[label] = sum(record["rounds"][label][-1] for record in districts.values())
                values.append((whole, options[label], 1, 1, first, 1))
                if final[label]:
                    values.append((whole, options[label], 1, last, final[label], 0))
            winner = max(final, key=lambda label: final[label])
            for label, option in options.items():
                for outcome in ("won",) if label == winner else ("lost",) if final[label] else ("eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            for unit, option, measure, number, votes, flags in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=flags,
                    value=votes,
                )
        return cs.write()
