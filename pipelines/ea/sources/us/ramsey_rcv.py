import json
import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ramsey_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ramsey County Elections"
WAYBACK = "https://web.archive.org/web/{}id_/{}"
NR = "http://www.co.ramsey.mn.us/NR/rdonlyres/"
ASSETS = "https://assets.ramseycountymn.gov/files/"
CONTESTS = {
    "2011-11-08:council:2": (
        "alged:2758000_2011_11_st. paul_MN_City Council_2_1",
        "20130325235654",
        NR + "A2ACDBE4-CC9B-4225-83AA-889D82563A08/26620/Ward2ReallocationSummaryStatementFinal.pdf",
    ),
    "2013-11-05:council:1": (
        "alged:2758000_2013_11_st. paul_MN_City Council_1_1",
        "20131129015337",
        NR + "781E3D5C-D105-4B01-BDE8-12322F4C2F51/35597/2013Ward1reallocationsummarystatementfinal.pdf",
    ),
    "2015-11-03:council:2": (
        "alged:2758000_2015_11_st. paul_MN_City Council_2_1",
        "20151123033754",
        "http://www.co.ramsey.mn.us:80/NR/rdonlyres/781E3D5C-D105-4B01-BDE8-12322F4C2F51/43874/2015Ward2Final1.pdf",
    ),
    "2019-11-05:council:1": (
        "alged:2758000_2019_11_st. paul_MN_City Council_1_1",
        "20201016020327",
        (
            "https://www.ramseycounty.us/sites/default/files/Elections%20and%20Voting/"
            "2019%20Ward%201%20Reallocation%20Summary%20Statement.pdf"
        ),
    ),
    "2019-11-05:council:6": (
        "alged:2758000_2019_11_st. paul_MN_City Council_6_1",
        "20201016020328",
        (
            "https://www.ramseycounty.us/sites/default/files/Elections%20and%20Voting/"
            "2019%20Ward%206%20Reallocation%20Summary%20Statement.pdf"
        ),
    ),
    "2023-11-07:council:1": (
        None,
        "20260817235545",
        ASSETS + "migrated-files/Reallocation_Summary_Round_5_Ward_1.pdf",
    ),
    "2023-11-07:council:3": (None, "20260817235545", ASSETS + "migrated-files/Reallocation_Summary_Ward_3.pdf"),
    "2023-11-07:council:7": (
        None,
        "20260817235548",
        ASSETS + "migrated-files/Reallocation_Summary_2023_-_Ward_7.pdf",
    ),
    "2025-11-04:mayor": (
        None,
        "20251112153503",
        ASSETS + "2025-11/St.%20Paul%20Mayoral%20Reallocation%2011-4-25.pdf",
    ),
}
PRECINCTS = {
    "2023-11-07:council:1": (
        "20260817235545",
        ASSETS + "migrated-files/Reallocation_1st_Choice_Hand_Count_2023_-_Ward_1.pdf",
    ),
    "2023-11-07:council:3": (
        "20260817235545",
        ASSETS + "migrated-files/Reallocation_2023_1st_Choice_Hand_Count_Ward_3.pdf",
    ),
    "2023-11-07:council:7": ("20260817235546", ASSETS + "migrated-files/Reallocation_2023_-_Ward_7.pdf"),
}
STALE = {"2023-11-07:council:3"}
HAND = (
    "Ramsey County Elections' reallocation summary statement for the contest, from its hand count of the ranked "
    "ballots. Round 1 is the hand count of first choices. In each later round, the county eliminated every candidate "
    "who could no longer win and moved those ballots to their next continuing choice. Each candidate's value is a "
    "running total, and a candidate eliminated in a round shows 0 in the next. Exhausted counts every ballot that is "
    "not counting for a candidate in that round: undervotes, overvotes, ballots with no continuing choice, and, in "
    "round 1, the ballots the hand count set aside for review until the first reallocation. The statement also gives "
    "the election-night machine count of first choices, which is left out here."
)
RCTAB = (
    "Ramsey County Elections' RCTab tabulation of the contest, run on election night, November 4, 2025. Each "
    "candidate's value is a running total; a candidate eliminated in a round shows 0 in the next. Exhausted "
    "counts every ballot not counting for a candidate in that round, including undervotes."
)
FIRST = (
    "Reallocation began from the Ramsey County Elections hand count of first choices by precinct on November 10, "
    "2023. Exhausted counts are ballots with no first choice for a candidate: undervotes, overvotes, and ballots "
    "set aside for review."
)
STALE_NOTE = (
    "The county statement repeats the round 1 count of 168 inactive ballots in round 2. Here, the round 2 "
    "exhausted value is 2,470: 13,396 ballots counted minus 10,926 votes for the two remaining candidates "
    "(13,396 − 10,926 = 2,470)."
)


def fetch() -> None:
    for stamp, url in [(stamp, url) for _, stamp, url in CONTESTS.values()] + list(PRECINCTS.values()):
        response = httpx.get(WAYBACK.format(stamp, url), timeout=120, follow_redirects=True)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"{url}: not a PDF")
        raw.store(
            SOURCE,
            WAYBACK.format(stamp, url),
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests: dict[str, dict[str, object]] = {}
    for key, (_, stamp, url) in CONTESTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, WAYBACK.format(stamp, url)).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        text = text.replace("‐", "-").replace("‑", "-")
        rows: dict[str, list[int]] = {}
        for line in text.splitlines():
            match = re.match(r"\s*(\S.*?)\s{2,}(-?[\d,]+(?:\.\d+)?%?(?:\s+.*)?)$", line)
            if match:
                rows[match.group(1).strip()] = [
                    0 if token == "N/A" else int(token.replace(",", ""))
                    for token in match.group(2).split()
                    if re.fullmatch(r"-?[\d,]+|N/A", token)
                ]
        if key == "2025-11-04:mayor":
            labels = [label for label in rows if label not in ("Active Ballots", "Inactive Ballots Total")]
            labels = labels[labels.index("Melvin Carter") :]
            total = rows["Total Number of Ballots"][0]
            rounds = [
                {label: rows[label][0] for label in labels},
                {label: rows[label][2] for label in labels},
            ]
            stated = [rows["Active Ballots"][0], rows["Active Ballots"][1]]
            exhausted = [rows["Inactive Ballots Total"][0], rows["Inactive Ballots Total"][2]]
            under = rows["Number of Undervotes (No Rankings)"][0]
            for tally, active, inactive in zip(rounds, stated, exhausted, strict=True):
                if sum(tally.values()) != active or active + inactive + under != total:
                    raise ValueError(f"{key}: {tally} against {active}, {inactive}, and {under}")
        else:
            lead = 4 if "Team 1" in text else 2
            names = list(rows)
            labels = [
                label
                for label in names[: names.index("Total Votes Cast")]
                if len(rows[label]) > lead
                and rows[label] != list(range(1, len(rows[label]) + 1))
                and not label.startswith(("Suspended", "Inactive"))
            ]

            def subtotals(values: list[int], lead: int = lead) -> list[int]:
                rest = values[lead:]
                totals = [values[lead - 1]]
                while rest:
                    if totals[-1] != 0 and len(rest) >= 2:
                        totals.append(rest[1])
                        rest = rest[2:]
                    else:
                        totals.append(rest[0])
                        rest = rest[1:]
                return totals

            columns = {label: subtotals(rows[label]) for label in labels}
            cast = subtotals(rows["Total Votes Cast"])
            count = len(cast)
            if count > 2 and cast[-1] == cast[-2] and all(values[-1] == values[-2] for values in columns.values()):
                count -= 1
            rounds = [
                {label: values[number] if number < len(values) else 0 for label, values in columns.items()}
                for number in range(count)
            ]
            for number, tally in enumerate(rounds):
                if sum(tally.values()) != cast[number]:
                    raise ValueError(f"{key} round {number + 1}: {sum(tally.values())} against {cast[number]}")
            suspended = next(
                (rows[label] for label in names if label.startswith("Suspended")),
                [0],
            )
            held = next((previous for previous, value in pairwise(suspended) if value < 0), 0)
            inactive = set(next(rows[label] for label in names if label.startswith("Inactive")))
            total = rows["Total Ballots"][lead - 1]
            if key not in STALE:
                for extra in (0, held):
                    if all(total + extra - sum(tally.values()) in inactive for tally in rounds[1:]):
                        total += extra
                        break
                else:
                    raise ValueError(f"{key}: no round reconciles with the inactive ballots")
        contests[key] = {
            "file": WAYBACK.format(stamp, url),
            "ballots": total,
            "rounds": [list(tally.items()) for tally in rounds],
        }
        if key in PRECINCTS:
            file = WAYBACK.format(*PRECINCTS[key])
            sheet = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, file).path), "-"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            (header,) = [line.split() for line in sheet.splitlines() if line.strip().startswith("Precinct ")]
            surnames = header[1 : header.index("Write-In") + 1]
            order = []
            for surname in surnames:
                (label,) = [
                    label
                    for label in rounds[0]
                    if label.split()[-1].casefold() == surname.casefold()
                    or ("write" in label.casefold() and surname == "Write-In")
                ]
                order.append(label)
            width = len(order)
            units: dict[str, tuple[int, dict[str, int]]] = {}
            for line in sheet.splitlines():
                match = re.match(r"\s*(St\. Paul W-\d+ P-\d+|TOTAL)\s+([\d,\s]+)$", line)
                if match is None:
                    continue
                values = [int(token.replace(",", "")) for token in match.group(2).split()]
                if (
                    len(values) != width + 5
                    or sum(values[:width]) != values[width]
                    or values[width] + values[width + 1] != values[width + 2]
                    or values[width + 2] + values[width + 3] != values[width + 4]
                ):
                    raise ValueError(f"{key}: {line}")
                units[match.group(1).upper()] = (values[-1], dict(zip(order, values[:width], strict=True)))
            ballots, counts = units.pop("TOTAL")
            if counts != rounds[0] or ballots != total:
                raise ValueError(f"{key}: the precinct totals differ from the statement")
            for label in order:
                if sum(cell[label] for _, cell in units.values()) != rounds[0][label]:
                    raise ValueError(f"{key}: precincts do not sum for {label}")
            contests[key]["precincts"] = {"file": file, "units": units}
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="St. Paul's ranked-choice contests that went past round 1, 2011 to 2025, with every official round "
            "from Ramsey County's reallocation statements",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        county = cs.area("us.county", "27123")
        city = cs.area("us.place", "2758000")
        whole = cs.unit("us.place", "2758000")
        mayor = cs.key("us.office", "MN:2758000:mayor")
        council = cs.key("us.body", "MN:2758000:council")
        if {city, whole, mayor, council} & cs.minted:
            raise ValueError("St. Paul, its mayor, and its council must already exist")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        irv = cs.key("us.electoral_system", "saint-paul:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv", "maxRankings": 6, "batchElimination": True},
            rules_version=1,
        )
        name(irv, "St. Paul ranked-choice voting")
        restated = {existing for existing, _, _ in CONTESTS.values() if existing is not None}

        people: dict[tuple[str, str], set[tuple[UUID, bool]]] = {}
        for text, person, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[city, county]],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, area == city))

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
        for contest, value in conn.execute(
            """select k.id, x.value from contest k join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
               join election e on e.id = k.election_id
               where x.value like 'alged:2758000_%%' and e.date_from >= '2011-11-01'"""
        ).fetchall():
            if value in restated:
                continue
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            cs.add("contest", id=contest, **dict(zip(columns, row or (), strict=True)), electoral_system_id=irv)
        done: set[UUID] = set()
        for key, (existing, _, _) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            kind, _, ward = key[11:].partition(":")
            rounds = [{label: votes for label, votes in tally} for tally in contests[key]["rounds"]]
            seat = "Mayor" if kind == "mayor" else f"City Council, Ward {ward}"
            district = city if kind == "mayor" else cs.area("us.local_district", f"27:2758000:council:district-{ward}")
            if district in cs.minted:
                raise ValueError(f"{key}: the ward must already exist")
            if existing is None:
                election = cs.key("us.election", f"{day}:MN:2758000")
                parent = cs.key("us.election", f"{day}:MN")
                if parent in cs.minted:
                    raise ValueError(f"{key}: {day}:MN must already exist")
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
                    name(election, f"{day.year} St. Paul local election")
                    cs.add("slug", path=f"us/mn/st-paul/{day}-local", entity_id=election)
                contest = cs.key("us.contest", f"{day}:MN:2758000:{kind}" + (f":{ward}" if ward else ""))
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office" if kind == "mayor" else "body_seats",
                    office_id=mayor if kind == "mayor" else None,
                    body_id=None if kind == "mayor" else council,
                    district_area_id=district,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=irv,
                    default_result_kind="counted",
                    term="full",
                )
                name(
                    contest,
                    f"{day.year} St. Paul mayoral election"
                    if kind == "mayor"
                    else f"{day.year} St. Paul City Council election, District {ward}",
                )
                cs.add(
                    "slug",
                    path=f"us/mn/st-paul/{day}-local/" + ("mayor" if kind == "mayor" else f"council-district-{ward}"),
                    entity_id=contest,
                )
            else:
                contest = cs.key("us.contest", existing)
                row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
                if contest in cs.minted or row is None:
                    raise ValueError(f"{key}: {existing} must already exist")
                record = dict(zip(columns, row, strict=True))
                cs.add("contest", id=contest, **record, electoral_system_id=irv)
                election = record["election_id"]
                previous = cs.key("us.alged.result_set", existing)
                if previous in cs.minted:
                    raise ValueError(f"{key}: {existing}'s result set must already exist")
                for values in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where id = %s""",
                    [previous],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
                                    "id",
                                    "election_id",
                                    "contest_id",
                                    "stage",
                                    "kind",
                                    "status",
                                    "source_id",
                                    "as_of",
                                    "completeness",
                                    "supersedes_id",
                                ),
                                values,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
            top = whole
            if kind != "mayor":
                top = cs.unit("us.local_district", f"27:2758000:council:district-{ward}")
                if top in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=top,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=f"27:2758000:council:district-{ward}",
                        area_relation="equals",
                        area_id=district,
                        kind="standard",
                    )

            title = f"{day:%B} {day.day}, {day.year} St. Paul municipal election: {seat}, " + (
                "RCTab tabulation" if kind == "mayor" else "reallocation summary statement"
            )
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=contests[key]["file"],
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, contests[key]["file"]), source)

            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                if "write" in label.casefold():
                    found = [
                        option
                        for (option,) in conn.execute(
                            "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                        ).fetchall()
                    ]
                    if len(found) > 1:
                        raise ValueError(f"{key}: {len(found)} write-in options")
                    options[label] = found[0] if found else cs.key("us.option", f"{contest}:write_in:scattered")
                    if not found:
                        cs.add(
                            "contest_option",
                            id=options[label],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Write-in",
                        )
                    continue
                words = spell(label)
                if existing is not None:
                    if (words[0], words[-1]) not in current:
                        raise ValueError(f"{key}: no option for {label}")
                    options[label] = current[words[0], words[-1]]
                    continue
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _ in known}
                local = {person for person, here in known if here}
                if len(local) == 1:
                    matches = local
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(label))
                    if person in cs.minted and person not in {p for found in people.values() for p, _ in found}:
                        cs.add("person", id=person)
                        name(person, label)
                    people.setdefault((words[0], words[-1]), set()).add((person, True))
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
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")

            result = cs.key(f"{SOURCE}.result_set", key)
            ballots = contests[key]["ballots"]
            values = [(top, None, 3, 0, ballots), (top, None, 4, 1, sum(rounds[0].values()))]
            for number, tally in enumerate(rounds, 1):
                values.append((top, None, 21, number, ballots - sum(tally.values())))
                values.extend((top, options[label], 1, number, votes) for label, votes in tally.items())
            notes = [("methodology", RCTAB if kind == "mayor" else HAND)]
            if key in STALE:
                notes.append(("caveat", STALE_NOTE))
            sets = [(result, True, source, notes, values)]
            if result in cs.minted:
                final = rounds[-1]
                winner = max(final, key=lambda label: final[label])
                for label, option in options.items():
                    for outcome in (
                        ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",)
                    ):
                        cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            if "precincts" in contests[key]:
                sheet = contests[key]["precincts"]
                title = f"{day:%B} {day.day}, {day.year} St. Paul municipal election: {seat}, first-choice hand count by precinct"
                counted = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=counted,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=sheet["file"],
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, sheet["file"]), counted)
                values = []
                for unit, (cast, counts) in sheet["units"].items():
                    code = f"{day}:MN:27123:{unit}"
                    precinct = cs.unit("us.precinct", code)
                    area = cs.area("us.precinct", code)
                    if area in cs.minted and area not in done:
                        done.add(area)
                        ((ward_number, number),) = re.findall(r"W-(\d+) P-(\d+)", unit)
                        geom, point = conn.execute(
                            """select encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex')
                               from area_code c join area_geometry g on g.area_id = c.area_id
                               where c.code = %s""",
                            [f"2022:MN:27123:RAMSEY:{unit}"],
                        ).fetchone() or (None, None)
                        if geom is None:
                            raise ValueError(f"{key}: no 2022 shape for {unit}")
                        cs.add("area", id=area, scheme_code="us.precinct")
                        cs.add("area_code", area_id=area, code=code, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=county)
                        name(area, f"St. Paul W-{ward_number} P-{int(number)}, Ramsey County")
                        cs.add(
                            "area_geometry",
                            area_id=area,
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                            geom=geom,
                            label_point=point,
                            quality="official",
                            source_id=None,
                        )
                        cs.add(
                            "reporting_unit",
                            id=precinct,
                            polity_id=polity,
                            scheme_code="us.precinct",
                            code=code,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                        cs.add(
                            "reporting_unit_area",
                            unit_id=precinct,
                            area_id=district,
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
                    values.extend(
                        [
                            (precinct, None, 3, 0, cast),
                            (precinct, None, 4, 1, sum(counts.values())),
                            (precinct, None, 21, 1, cast - sum(counts.values())),
                            *((precinct, options[label], 1, 1, votes) for label, votes in counts.items()),
                        ]
                    )
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:precincts"),
                        False,
                        counted,
                        [("methodology", FIRST)],
                        values,
                    )
                )
            for result_set, default, origin, remarks, rows in sets:
                if result_set not in cs.minted:
                    continue
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=origin,
                    is_default=default,
                    completeness=1,
                )
                for remark, body in remarks:
                    cs.add("note", entity_id=result_set, kind=remark, body_md=body)
                for unit, option, measure, number, votes in rows:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
        return cs.write()
