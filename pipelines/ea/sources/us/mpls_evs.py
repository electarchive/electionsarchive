import csv
import difflib
import io
import json
import re
import subprocess
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mpls_evs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Minneapolis Elections & Voter Services"
BASE = "https://vote.minneapolismn.gov/media/-www-content-assets/documents/archive/elections/"
PLANS = {"2009": "2008-11-04", "2013": "2012-11-06", "2017": "2016", "2021": "2020"}
CONTESTS: dict[str, tuple[str, str, str]] = {
    **{
        f"2009-11-03:MN:minneapolis:city-council:{ward}": (
            f"alged:2743000_2009_11_minneapolis_MN_City Council_{ward}_1",
            f"2009-Council-Ward-{ward:02}-Final-RCV.xls",
            f"2009-Council-Ward-{ward}-CVR.xls",
        )
        for ward in (4, 5)
    },
    "2013-11-05:MN:minneapolis:mayor": (
        "alged:2743000_2013_11_minneapolis_MN_Mayor_mayor_1",
        "2013-Mayor-RCV-Tabulation-Summary.xlsx",
        "2013-Mayor-CVR.xlsx",
    ),
    **{
        f"2013-11-05:MN:minneapolis:city-council:{ward}": (
            f"alged:2743000_2013_11_minneapolis_MN_City Council_{ward}_1",
            f"2013-City-Council-Ward{ward}-Final-RCV.xlsx",
            f"2013-Council-Ward-{ward}-CVR.xlsx",
        )
        for ward in (5, 9, 13)
    },
    "2017-11-07:MN:minneapolis:mayor": (
        "alged:2743000_2017_11_minneapolis_MN_Mayor_mayor_1",
        "2017-Mayor-Results-Summary.pdf",
        "2017-Mayor-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:1": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_1_1",
        "2017-Ward-1-Results-Summary.pdf",
        "2017-Ward-1-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:3": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_3_1",
        "2017-Ward-3-Results-Summary.pdf",
        "2017-Ward-3-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:4": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_4_1",
        "2017-Ward-4-Results-Summary.pdf",
        "2017-Ward-4-Ballot-Records.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:5": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_5_1",
        "2017-Ward-5-RCV-Tabulation-Summary.pdf",
        "2017-Council-Ward-5-CVR.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:9": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_9_1",
        "2017-Ward-9-Race-Results-Summary.pdf",
        "2017-Ward-9-Ballot-Records-.csv",
    ),
    "2017-11-07:MN:minneapolis:city-council:11": (
        "alged:2743000_2017_11_minneapolis_MN_City Council_11_1",
        "2017-Ward-11-Results-Summary.pdf",
        "2017-Ward-11-Ballot-Records.csv",
    ),
    "2021-11-02:MN:minneapolis:mayor": (
        "alged:2743000_2021_11_minneapolis_MN_Mayor_mayor_1",
        "2021-Mayor-Round-Results-Summary.pdf",
        "2021-Mayor-Cast-Vote-Record.csv",
    ),
    **{
        f"2021-11-02:MN:minneapolis:city-council:{ward}": (
            f"alged:2743000_2021_11_minneapolis_MN_City Council_{ward}_1",
            f"2021-Council-Ward-{ward}-Round-Results-Summary.pdf",
            f"2021-Ward-{ward}-Cast-Vote-Record.csv",
        )
        for ward in (1, 2, 3, 5, 10)
    },
}
SKIPPED = {"undervote", "xxx", ""}

OFFICIAL = (
    "Elections & Voter Services' ranked-choice tabulation summary statement for the contest. Minneapolis defeats "
    "every candidate who can no longer win in one round, so a round can drop several. Each candidate's value at "
    "round n is their running total; an eliminated candidate has 0 at the round of their elimination. Ballots "
    "exhausted in a round are the statement's exhausted ballots; the statement leaves out undervotes, defective "
    "ballots, and spoiled ballots."
)
DERIVED = (
    "Tabulated by electionsarchive.com from Elections & Voter Services' cast vote record, which gives each "
    "ballot's three rankings and its precinct. A skipped or repeated ranking is passed over, a ballot exhausts at an "
    "overvote, and ballots with no ranking are left out, as Minneapolis's rules have them. Each round drops the "
    "candidates the official count dropped. Summed over the precincts, every round reproduces the tabulation "
    "summary statement exactly."
)


def fetch() -> None:
    for name in [file for _, *files in CONTESTS.values() for file in files]:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, BASE + name, suffix="." + name.rsplit(".", 1)[1])
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, BASE + name, suffix="." + name.rsplit(".", 1)[1])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    official = {}
    frames = []
    for key, (_, summary, record) in CONTESTS.items():
        path = raw.cached(SOURCE, BASE + summary).path
        table: list[tuple[str, list[str]]] = []
        if summary.endswith(".pdf"):
            lines = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            start = next(index for index, line in enumerate(lines) if re.match(r"^\s*Candidate\s{2,}", line))
            head: list[str] = []
            for line in lines[start + 1 :]:
                if line.strip().startswith("TOTAL"):
                    break
                match = re.match(r"^(.*?)\s*((?:\s+(?:-?[\d,]+(?:\.\d+)?%?|-))+)\s*$", line)
                if match is None or not re.search(r"\d", match[2]):
                    if line.strip() and not line.strip().startswith("BALANCED"):
                        head.append(line.strip())
                    continue
                label = " ".join([*head, match[1].strip()]).strip()
                head = []
                table.append((label, [token for token in match[2].split() if not token.endswith("%")]))
            if head:
                raise ValueError(f"{key}: unmatched lines {head}")
        else:
            workbook = fastexcel.read_excel(str(path))
            sheet = (
                "Round Results Summary" if "Round Results Summary" in workbook.sheet_names else workbook.sheet_names[0]
            )
            rows = [
                ["" if cell is None else str(cell).strip() for cell in row]
                for row in workbook.load_sheet(sheet, header_row=None, dtypes="string").to_polars().iter_rows()
            ]
            start, column = next(
                (index, row.index("Candidate")) for index, row in enumerate(rows) if "Candidate" in row
            )
            for row in rows[start + 1 :]:
                if row[column] == "TOTAL":
                    break
                if row[column]:
                    table.append((row[column], row[column + 1 : -1]))
        rounds: list[dict[str | None, float]] = []
        for label, tokens in table:
            values = [tokens[0], *tokens[2::2]]
            for number, token in enumerate(values):
                if number and token in ("-", ""):
                    continue
                while len(rounds) <= number:
                    rounds.append({})
                votes = 0 if token in ("-", "") else float(token.replace(",", ""))
                rounds[number][None if label == "Exhausted" else label] = votes
        rounds = [
            {
                label: votes
                for label, votes in tally.items()
                if number == 0 or votes or label is None or label in rounds[number - 1] and rounds[number - 1][label]
            }
            for number, tally in enumerate(rounds)
        ]
        if len({round(sum(tally.values()), 2) for tally in rounds}) != 1:
            raise ValueError(f"{key}: the rounds do not add up to one total, {[sum(t.values()) for t in rounds]}")
        if len(rounds) == 1:
            raise ValueError(f"{key}: decided in round 1")
        official[key] = rounds

        path = raw.cached(SOURCE, BASE + record).path
        if record.endswith(".csv"):
            data = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))
        else:
            workbook = fastexcel.read_excel(str(path))
            data = [
                ["" if cell is None else str(cell).strip() for cell in row]
                for row in workbook.load_sheet(workbook.sheet_names[0], header_row=None, dtypes="string")
                .to_polars()
                .iter_rows()
            ]
        start = next(
            index for index, row in enumerate(data) if row and re.match(r"(precinct|w-p)", row[0], re.IGNORECASE)
        )
        labels = [label for label in rounds[0] if label is not None]
        names: dict[str, str | None] = {}
        for row in data[start + 1 :]:
            for choice in row[1:4]:
                choice = choice.strip()
                if choice.casefold() in SKIPPED or choice.casefold() == "overvote" or choice in names:
                    continue
                found = [
                    label
                    for label in labels
                    if label.casefold() == choice.casefold()
                    or choice.upper() == "UWI"
                    and label == "Undeclared Write-ins"
                    or len(choice) == 3
                    and choice.isupper()
                    and re.sub(r"[^A-Z]", "", label.split()[-1].upper())[:3] == choice
                ]
                if not found:
                    found = [
                        label
                        for label in labels
                        if label.casefold().split()[0] == choice.casefold().split()[0]
                        and label.casefold().split()[-1] == choice.casefold().split()[-1]
                    ]
                if not found:
                    found = difflib.get_close_matches(
                        choice.casefold(), [label.casefold() for label in labels], 1, 0.85
                    )
                    found = [label for label in labels if label.casefold() in found]
                if not found:
                    found = [label for label in labels if "write" in label.casefold()] or [None]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} summary rows for the ranking {choice!r}")
                names[choice] = found[0]
        ballots: dict[tuple[str, tuple[str | None, ...]], int] = {}
        for row in data[start + 1 :]:
            if len(row) < 4 or not row[0].strip():
                continue
            ((ward, precinct),) = re.findall(
                r"^(?:MINNEAPOLIS )?W?-?(\d+)[- ]P?-?(\w+)$", row[0].strip(), re.IGNORECASE
            )
            unit = f"W-{int(ward)} P-{precinct.lstrip('0') or '0'}"
            ranking = tuple(
                None
                if choice.strip().casefold() in SKIPPED or choice.strip().casefold() == "overvote"
                else names[choice.strip()]
                for choice in row[1:4]
            )
            count = int(float(row[4])) if len(row) > 4 and row[4].strip() else 1
            ballots[unit, ranking] = ballots.get((unit, ranking), 0) + count
        counts: dict[tuple[str, int, str | None], int] = {}
        for (unit, ranking), count in ballots.items():
            tops = [
                next(
                    (
                        choice
                        for choice in ranking
                        if choice is not None and choice in standing and (number == 1 or standing[choice])
                    ),
                    None,
                )
                for number, standing in enumerate(rounds, 1)
            ]
            if tops[0] is None:
                continue
            for number, top in enumerate(tops, 1):
                counts[unit, number, top] = counts.get((unit, number, top), 0) + count
        tally = pl.DataFrame(
            [
                (unit, number, label, counts.get((unit, number, label), 0))
                for unit in sorted({unit for unit, _ in ballots})
                for number, standing in enumerate(rounds, 1)
                for label in {*standing, *(rounds[number - 2] if number > 1 else ())}
            ],
            schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
            orient="row",
        )
        sums = tally.group_by("round", "label").agg(pl.col("votes").sum())
        for number, expected in enumerate(rounds, 1):
            found = {label: votes for _, label, votes in sums.filter(pl.col("round") == number).iter_rows() if votes}
            if found != {label: votes for label, votes in expected.items() if votes}:
                raise ValueError(
                    f"{key} round {number}: cast vote record and summary differ on "
                    f"{ {label: (found.get(label), expected.get(label)) for label in {*found, *expected} if found.get(label, 0) != expected.get(label, 0)} }"
                )
        frames.append(tally.with_columns(key=pl.lit(key)))
    (PARSED / "official.json").write_text(
        json.dumps(
            {
                key: [[[label, votes] for label, votes in tally.items()] for tally in rounds]
                for key, rounds in official.items()
            }
        )
    )
    pl.concat(frames).write_parquet(PARSED / "tallies.parquet")

    rows = []
    with connect() as conn:
        for vintage, prefix in PLANS.items():
            for text, area, geom, point in conn.execute(
                """select n.text, a.id, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex')
                   from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent p on p.area_id = a.id join area_code h on h.area_id = p.parent_id and h.code = '27053'
                   join area_geometry g on g.area_id = a.id
                   where a.scheme_code = 'us.precinct' and c.code like %s and n.text ilike 'Minneapolis W-%%'""",
                [prefix + ":MN:%"],
            ).fetchall():
                ((ward, precinct),) = re.findall(r"^Minneapolis W-\s*(\d+) P-\s*(\w+),", text)
                rows.append((vintage, f"W-{int(ward)} P-{precinct.lstrip('0') or '0'}", str(area), geom, point))
    frame = pl.DataFrame(rows, schema=["plan", "unit", "area", "geom", "point"], orient="row")
    if frame.select("plan", "unit").is_duplicated().any():
        raise ValueError("two areas share a precinct in one plan")
    frame.write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    official = {
        key: [{label: int(votes) for label, votes in tally} for tally in rounds]
        for key, rounds in json.loads((PARSED / "official.json").read_text()).items()
    }
    tallies = pl.read_parquet(PARSED / "tallies.parquet")
    shapes = {
        (plan, unit): (geom, point)
        for plan, unit, _, geom, point in pl.read_parquet(PARSED / "precincts.parquet").iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minneapolis's ranked-choice contests for mayor and city council that went past round 1, 2009 to "
            "2021, with every round by precinct tabulated from the cast vote records",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        county = cs.area("us.county", "27053")
        city = cs.area("us.place", "2743000")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchone() or (
            None,
        )
        if polity is None:
            raise ValueError("Minneapolis must already be a polity")
        (special,) = conn.execute(
            """select e.id from election e join external_id x on true join contest k on k.id = x.entity_id
               where x.scheme = 'us.contest' and x.value = 'alged:2743000_2020_8_minneapolis_MN_City Council_6_1'
               and e.id = k.election_id"""
        ).fetchone() or (None,)
        if special is None:
            raise ValueError("the 2020 Ward 6 special must already exist")
        cs.add(
            "election",
            **dict(
                zip(
                    ("id", "polity_id", "parent_id", "kind", "status", "rerun_of_id"),
                    conn.execute(
                        "select id, polity_id, parent_id, kind, status, rerun_of_id from election where id = %s",
                        [special],
                    ).fetchone()
                    or (),
                    strict=True,
                )
            ),
            date_from=date(2020, 8, 11),
            date_to=date(2020, 8, 11),
            date_precision="day",
            original_calendar=None,
            original_text=None,
        )
        irv = cs.key("us.electoral_system", "minneapolis:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv", "maxRankings": 3, "batchElimination": True},
            rules_version=1,
        )
        name(irv, "Minneapolis ranked-choice voting")

        precincts: dict[str, dict[str, UUID]] = {}
        for key, unit in sorted(set(tallies.select("key", "unit").iter_rows())):
            day, plan = key[:10], key[:4]
            if unit in precincts.get(day, {}):
                continue
            area = cs.area("us.precinct", f"{day}:MN:27053:{unit}")
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code=f"{day}:MN:27053:{unit}", valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=county)
            name(area, f"Minneapolis {unit}, Hennepin County")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
                geom=shapes[plan, unit][0],
                label_point=shapes[plan, unit][1],
                quality="official",
                source_id=None,
            )
            code = f"{day}:MN:27053:{unit}"
            precincts.setdefault(day, {})[unit] = cs.unit("us.precinct", code)
            cs.add(
                "reporting_unit",
                id=precincts[day][unit],
                polity_id=polity,
                scheme_code="us.precinct",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
            )
        for day in sorted(precincts):
            cs.add(
                "boundary_set",
                scheme_code="us.precinct",
                vintage=day,
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
                as_of=date.fromisoformat(day),
            )

        people: dict[tuple[str, str], set[tuple[UUID, date, bool]]] = {}
        for text, person, held, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, e.date_from, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[county, city]],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add((person, held, area == city))

        for key, (existing, summary, record) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            rounds = official[key]
            contest = cs.key("us.contest", existing)
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
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            if contest in cs.minted or row is None:
                raise ValueError(f"{key}: {existing} must already exist")
            contest_row = dict(zip(columns, row, strict=True))
            cs.add("contest", id=contest, **contest_row, electoral_system_id=irv)
            election, district = contest_row["election_id"], contest_row["district_area_id"]
            previous = cs.key("us.alged.result_set", existing)
            if previous in cs.minted:
                raise ValueError(f"{key}: {existing}'s result set must already exist")
            tops = conn.execute(
                """select distinct u.id from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k where r.id = %s""",
                [previous],
            ).fetchall()
            if len(tops) != 1:
                raise ValueError(f"{key}: {existing}'s result set has {len(tops)} reporting units")
            office = key.split(":", 3)[3]
            seat = "Mayor" if office == "mayor" else f"City Council, Ward {office.split(':')[1]}"
            sources = []
            for file, kind in ((summary, "tabulation summary statement"), (record, "cast vote record")):
                title = f"{day:%B} {day.day}, {day.year} Minneapolis municipal election: {seat}, {kind}"
                source = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=BASE + file,
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, BASE + file), source)
                sources.append(source)

            current = conn.execute(
                """select o.id, o.ballot_label, h.pipeline = %s from contest_option o
                   join entity n on n.id = o.id join changeset h on h.id = n.changeset_id
                   where o.contest_id = %s and o.kind = 'candidate'""",
                [SOURCE, contest],
            ).fetchall()
            options: dict[str, UUID] = {}
            scattered = None
            for label in (label for label in rounds[0] if label is not None):
                if "write" in label.casefold() or len(spell(label)) == 1:
                    if scattered is None:
                        found = [
                            option
                            for (option,) in conn.execute(
                                "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                            ).fetchall()
                        ]
                        if len(found) > 1:
                            raise ValueError(f"{key}: {len(found)} write-in options")
                        scattered = found[0] if found else cs.key("us.option", f"{key}:write_in:scattered")
                        if not found:
                            cs.add(
                                "contest_option",
                                id=scattered,
                                contest_id=contest,
                                kind="write_in",
                                ballot_label="Write-in",
                            )
                    options[label] = scattered
                    continue
                display = " ".join(reversed(label.split(", "))) if re.fullmatch(r"[^,]+, [^,]+", label) else label
                words = spell(display)
                spelled = {option: spell(text) for option, text, _ in current}
                found = [option for option, other in spelled.items() if other[-1] == words[-1]]
                if len(found) > 1 or [
                    other for other in rounds[0] if other is not None and spell(other)[-1] == words[-1]
                ] != [label]:
                    found = [option for option in found if spelled[option][0] == words[0]]
                if len(found) > 1:
                    found = [option for option, _, mine in current if mine and option in found] or found
                if len(found) == 1:
                    options[label] = found[0]
                    continue
                if found:
                    raise ValueError(f"{key}: {len(found)} options for {label}")
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _, _ in known}
                for preferred in (
                    {person for person, held, _ in known if held == day},
                    {person for person, _, local in known if local},
                ):
                    if len(preferred) == 1:
                        matches = preferred
                        break
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} people for {label}")
                if matches:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(display))
                    cs.add("person", id=person)
                    name(person, display)
                    people.setdefault((words[0], words[-1]), set()).add((person, day, True))
                options[label] = cs.key("us.option", f"{key}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len({option for option in options.values() if option != scattered}) != len(
                [option for option in options.values() if option != scattered]
            ):
                raise ValueError(f"{key}: two candidates share an option")

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
            result = cs.key(f"{SOURCE}.result_set", key)
            final = rounds[-1]
            winner = max((label for label in final if label is not None), key=lambda label: final[label])
            for option in set(options.values()):
                held = [label for label, other in options.items() if other == option]
                for kind in (
                    ("won", "elected")
                    if winner in held
                    else ("lost" if any(final.get(label) for label in held) else "eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=kind)
            rows = tallies.filter(pl.col("key") == key)
            units = {"top": str(tops[0][0]), **{unit: str(precincts[key[:10]][unit]) for unit in rows["unit"].unique()}}
            if office != "mayor":
                cs.frame(
                    "reporting_unit_area",
                    pl.DataFrame(
                        {
                            "unit_id": [units[unit] for unit in rows["unit"].unique().sort()],
                            "area_id": str(district),
                            "valid_from": day,
                            "valid_to": day + timedelta(days=1),
                        }
                    ),
                )
            sets = [
                (
                    result,
                    True,
                    OFFICIAL,
                    sources[0],
                    0,
                    pl.DataFrame(
                        [
                            ("top", number, label, tally.get(label, 0))
                            for number, tally in enumerate(rounds, 1)
                            for label in rounds[0]
                            if label in tally
                        ],
                        schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                        orient="row",
                    ),
                ),
                (
                    cs.key(f"{SOURCE}.result_set", f"{key}:precincts"),
                    False,
                    DERIVED,
                    sources[1],
                    1,
                    rows.select("unit", "round", "label", "votes"),
                ),
            ]
            for result_set, default, note, source, flags, values in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if result_set not in cs.minted:
                    continue
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                unknown = set(values["label"].drop_nulls()) - set(options)
                if unknown:
                    raise ValueError(f"{key}: no options for {unknown}")
                frame = values.with_columns(unit_id=pl.col("unit").replace_strict(units))
                first = frame.filter(pl.col("round") == 1)
                frame = pl.concat(
                    [
                        frame,
                        first.group_by("unit", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "unit", round=pl.lit(0, pl.Int16), label=pl.lit("ballots"), votes="votes", unit_id="unit_id"
                        ),
                        first.filter(pl.col("label").is_not_null())
                        .group_by("unit", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "unit", round=pl.lit(1, pl.Int16), label=pl.lit("valid"), votes="votes", unit_id="unit_id"
                        ),
                    ]
                )
                cs.frame(
                    "result_value",
                    frame.group_by(
                        unit_id="unit_id",
                        option_id=pl.col("label").replace_strict(
                            {label: str(option) for label, option in options.items()}, default=None
                        ),
                        measure=pl.when(pl.col("label").is_null())
                        .then(21)
                        .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                        .cast(pl.Int16),
                        count_round=pl.col("round").cast(pl.Int16),
                    )
                    .agg(value=pl.col("votes").sum())
                    .select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit_id",
                        option_id="option_id",
                        channel=pl.lit(0, pl.Int16),
                        measure="measure",
                        count_round="count_round",
                        flags=pl.lit(flags, pl.Int16),
                        value="value",
                    ),
                )
        return cs.write()
