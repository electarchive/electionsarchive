import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.albany_stv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alameda County Registrar of Voters"
BASE = "https://alamedacountyca.gov/rovresults/rcv/{}/Albany/{}/RcvDetailedReport.xml"
CONTESTS = {
    "2022:council": ("248", "001-CityCouncil", "2022 Albany City Council election", 2),
    "2022:school": (
        "248",
        "002-BoardOfEducation",
        "2022 Albany City Unified School District Board of Education election",
        3,
    ),
    "2024:council": ("252", "001-CityCouncil", "2024 Albany City Council election", 3),
    "2024:school": (
        "252",
        "002-BoardOfEducation",
        "2024 Albany City Unified School District Board of Education election",
        2,
    ),
}
RESTATED = (
    "2020 Albany City Council election",
    "2020 Albany City Unified School District Board of Education election",
)
PER_ROUND = "Official rounds from the Alameda County Registrar of Voters' certified RCV detailed report. Round 1 is the tally of first choices. In each round, a candidate at or above the threshold is elected and their surplus moves to the next continuing choices at a fractional value. Otherwise, the last-place candidate is eliminated and their ballots move at their current value. The threshold is recomputed each round as the continuing votes divided by one more than the number of seats, plus one, ignoring fractions. Each candidate's value is a running total; an elected candidate holds the threshold once their surplus moves, and an eliminated candidate shows 0 in the next round. Invalid counts are blank ballots and ballots overvoted at the first choice. Exhausted counts are valid ballots with no continuing choice, including fractional remainders."
FIXED = "Official rounds from the Alameda County Registrar of Voters' certified RCV detailed report. Round 1 is the tally of first choices. In each round, a candidate at or above the threshold was elected, and their surplus moved to the next continuing choices on their ballots at a fractional value; otherwise the last-place candidate was eliminated, and their ballots moved at their current value. The threshold was set in round 1 as the valid ballots divided by one more than the number of seats, plus one, ignoring fractions, and stayed fixed. Each candidate's value is a running total; an elected candidate holds the threshold once their surplus moves, and an eliminated candidate shows 0 in the next round. Invalid counts are blank ballots and ballots overvoted at the first choice. Exhausted counts are valid ballots with no continuing choice, including fractional remainders."


def fetch() -> None:
    for election, folder, _, _ in CONTESTS.values():
        raw.fetch(SOURCE, BASE.format(election, folder), suffix=".xml")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {}
    for key, (election, folder, _, seats) in CONTESTS.items():
        root = ET.parse(raw.cached(SOURCE, BASE.format(election, folder)).path).getroot()
        title = next(item for item in root.iter() if item.get("Name") == "Title")
        if title.get("Textbox8", "").casefold() != "certified final results":
            raise ValueError(f"{key}: {title.get('Textbox8')}")
        settings = next(item for item in root.iter() if item.tag.endswith("Tablix3"))
        if settings.get("Textbox36") != "STV":
            raise ValueError(f"{key}: not STV")
        basis = settings.get("Textbox30")
        if basis not in ("Continuing ballots per round", "Continuing ballots 1st round"):
            raise ValueError(f"{key}: threshold basis {basis}")
        if f"Number of positions to elect is {seats}." not in ET.tostring(root, encoding="unicode"):
            raise ValueError(f"{key}: not {seats} seats")
        rounds: list[dict[str, float]] = []
        thresholds: list[int] = []
        ballots: set[float] = set()
        firsts: dict[str, float] = {}
        for step in root.iter():
            if step.get("Textbox86") is None:
                continue
            votes = {
                str(choice.get("choiceName2")): float(str(choice.get("votes")))
                for choice in step.iter()
                if choice.get("choiceName2") is not None
            }
            if not firsts:
                firsts = votes
            tally = {
                label: value
                for label, value in votes.items()
                if label not in ("Blanks", "Exhausted", "Overvotes", "Remainder Points", "Surplus Transfer Remainder")
            }
            if rounds:
                for label, value in rounds[-1].items():
                    if label not in tally and value:
                        tally[label] = 0.0
                tally = {label: value for label, value in tally.items() if value or rounds[-1].get(label)}
            if abs(sum(tally.values()) - float(str(step.get("continuingVotes")))) > 0.001:
                raise ValueError(f"{key}: round {len(rounds) + 1} does not sum")
            ballots.add(
                round(float(str(step.get("continuingVotes"))) + float(str(step.get("nonTransferableVotes"))), 3)
            )
            (threshold,) = re.findall(r"Threshold: ([\d,]+)\.0+", str(step.get("Textbox86")))
            thresholds.append(int(threshold.replace(",", "")))
            rounds.append(tally)
        if max(ballots) - min(ballots) > 0.01:
            raise ValueError(f"{key}: ballots change between rounds, {ballots}")
        for number, tally in enumerate(rounds):
            continuing = sum(tally.values()) if basis.endswith("per round") or number == 0 else sum(rounds[0].values())
            if thresholds[number] != int(continuing / (seats + 1)) + 1:
                raise ValueError(f"{key}: round {number + 1} threshold {thresholds[number]}")
        elected = list(
            dict.fromkeys(
                text.split(" is elected because")[0]
                for item in root.iter()
                if " is elected because" in (text := item.get("Textbox21") or "")
            )
        )
        if len(elected) != seats:
            raise ValueError(f"{key}: {len(elected)} elected for {seats} seats")
        contests[key] = {
            "ballots": round(min(ballots)),
            "invalid": round(firsts["Blanks"] + firsts["Overvotes"]),
            "per_round": basis.endswith("per round"),
            "elected": elected,
            "rounds": rounds,
        }
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Albany, California's single transferable vote council and school board elections, 2022 and 2024, "
            "with every official round, and the 2020 contests restated as STV",
            polity="US",
        )

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "0600674")
        units = {"council": cs.unit("us.place", "0600674"), "school": cs.unit("us.school_district", "0601860")}
        if {city, *units.values()} & cs.minted:
            raise ValueError("Albany must already exist")
        ((town,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        ((school,),) = conn.execute(
            """select p.id from polity p join name n on n.entity_id = p.id
               where n.text = 'Albany City Unified School District' and p.kind = 'school_district'"""
        ).fetchall()
        systems = {
            town: cs.key("us.electoral_system", "albany:stv"),
            school: cs.key("us.electoral_system", "albany-unified:stv"),
        }
        for polity, label in ((town, "Albany"), (school, "Albany Unified School District")):
            cs.add(
                "electoral_system",
                id=systems[polity],
                polity_id=polity,
                rules={"formula": "stv", "quota": "droop"},
                rules_version=1,
            )
            cs.add(
                "name",
                entity_id=systems[polity],
                text=f"{label} single transferable vote",
                lang="en",
                kind="official",
                is_preferred=True,
            )
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
        found: dict[str, tuple[UUID, UUID, UUID]] = {}
        for heading in (*RESTATED, *(title for _, _, title, _ in CONTESTS.values())):
            rows = conn.execute(
                """select k.id, k.election_id, e.polity_id from contest k join name n on n.entity_id = k.id
                   join election e on e.id = k.election_id where n.text = %s and e.polity_id in (%s, %s)""",
                [heading, town, school],
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(f"{len(rows)} contests named {heading}")
            ((contest, election, polity),) = rows
            found[heading] = (contest, election, polity)
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            cs.add(
                "contest", id=contest, **dict(zip(columns, row or (), strict=True)), electoral_system_id=systems[polity]
            )

        sources: dict[str, UUID] = {}
        for key, (number, folder, heading, seats) in CONTESTS.items():
            contest, election, polity = found[heading]
            record = contests[key]
            url = BASE.format(number, folder)
            title = f"RCV detailed report, {'Members, City Council' if 'Council' in folder else 'Members, Board of Education'} - Albany, {key[:4]} general election"
            sources[key] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[key])
            for values in conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                     supersedes_id from result_set where contest_id = %s and is_default
                     and changeset_id not in (select id from changeset where pipeline = %s)""",
                [contest, SOURCE],
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
                words = spell(label)
                if (words[0], words[-1]) not in current:
                    raise ValueError(f"{key}: no option for {label}")
                options[label] = current[words[0], words[-1]]
            if len(set(options.values())) != len(options) or len(options) != len(current):
                raise ValueError(f"{key}: options do not line up")
            result = cs.key(f"{SOURCE}.result_set", key)
            if result not in cs.minted:
                continue
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[key],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=PER_ROUND if record["per_round"] else FIXED)
            valid = sum(rounds[0].values())
            if record["ballots"] - record["invalid"] != round(valid):
                raise ValueError(f"{key}: ballots, invalid, and valid disagree")
            values = [(None, 3, 0, record["ballots"]), (None, 5, 0, record["invalid"]), (None, 4, 1, round(valid))]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, round(valid - sum(tally.values()), 4)))
                values.extend((options[label], 1, number, round(votes, 4)) for label, votes in tally.items())
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=units[key[5:]],
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=votes,
                )
            final = rounds[-1]
            for label, option in options.items():
                for outcome in (
                    ("won", "elected")
                    if label in record["elected"]
                    else ("lost" if final.get(label) else "eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
