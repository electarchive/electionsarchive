import json
import re
import subprocess
import unicodedata
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.arlington_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Arlington County Office of Voter Registration and Elections"
SUMMARY = "https://vote.arlingtonva.gov/files/assets/vote/v/1/elections/electionresults/rcv-summary.pdf"
CAPTURE = f"https://web.archive.org/web/20240706000509id_/{SUMMARY}"
TITLE = "Ranked choice voting results summary, June 18, 2024 Democratic primary"
CONTEST = "2024 Arlington County County Board election, Democratic primary"
METHOD = "From the county's official ranked-choice summary. Round 1 inactive ballots had no valid first choice and are counted as invalid; ballots that became inactive in later rounds are counted as exhausted."
DEMOTED = "This set uses only first-choice votes from the state archive and is missing a few votes compared to the county's final count. The county's round-by-round count is the default."


def fetch() -> None:
    raw.fetch(SOURCE, CAPTURE, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CAPTURE).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    (cast,) = re.findall(r"Total Votes Cast\s+([\d,]+)", text)
    (line,) = re.findall(r"(?m)^Threshold\*\s+(.+)$", text)
    thresholds = [int(value.replace(",", "")) for value in line.split()]
    rows: dict[str, list[int]] = {}
    for label, figures in re.findall(
        r"(?m)^(\S.*?)\s{2,}((?:[\d,]+|Redistributed)(?:\s+(?:[\d,]+|Redistributed))*)$", text
    ):
        if label.startswith(("Threshold", "Total")):
            continue
        rows[label.strip()] = [int(value.replace(",", "")) for value in figures.split() if value != "Redistributed"]
    inactive = rows.pop("Inactive Ballots**")
    rounds = [
        {label: figures[number] for label, figures in rows.items() if number < len(figures)}
        for number in range(len(thresholds))
    ]
    for number, tally in enumerate(rounds):
        if thresholds[number] != sum(tally.values()) // 2 + 1:
            raise ValueError(f"round {number + 1} threshold {thresholds[number]} does not fit {sum(tally.values())}")
        if sum(tally.values()) + inactive[number] != int(cast.replace(",", "")):
            raise ValueError(f"round {number + 1} does not add up to the votes cast")
    (winner,) = re.findall(r"(?m)^Winner\s{2,}(.+)$", text)
    if max(rounds[-1], key=lambda label: rounds[-1][label]) != winner.strip():
        raise ValueError("winner does not lead the last round")
    record = {"ballots": int(cast.replace(",", "")), "inactive": inactive, "rounds": rounds}
    (PARSED / "contests.json").write_text(json.dumps({"2024-06-18": record}))


def emit() -> Path:
    record = json.loads((PARSED / "contests.json").read_text())["2024-06-18"]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arlington County, Virginia's June 2024 Democratic primary for County Board, round by round from "
            "the county's ranked-choice summary, restated as IRV",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        ((contest, election, polity),) = conn.execute(
            """select k.id, k.election_id, e.polity_id from contest k join name n on n.entity_id = k.id
               join election e on e.id = k.election_id where n.text = %s""",
            [CONTEST],
        ).fetchall()
        top = cs.unit("us.county", "51013")
        if top in cs.minted:
            raise ValueError("Arlington County's reporting unit must already exist")
        irv = cs.key("us.electoral_system", "arlington-va:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Arlington County ranked-choice voting")
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
        (row,) = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchall()
        cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=irv)
        fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness")
        fields += ("supersedes_id",)
        for values in conn.execute(
            f"""select {", ".join(fields)} from result_set where contest_id = %s
                and changeset_id not in (select id from changeset where pipeline = %s)""",
            [contest, SOURCE],
        ).fetchall():
            cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
            cs.add("note", entity_id=values[0], kind="caveat", body_md=DEMOTED)

        current = {
            tuple(spell(text)[:1] + spell(text)[-1:]): option
            for option, text in conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'", [contest]
            ).fetchall()
        }
        rounds = record["rounds"]
        options = {label: current[tuple(spell(label)[:1] + spell(label)[-1:])] for label in rounds[0]}
        if len(set(options.values())) != len(options) or len(options) != len(current):
            raise ValueError("options do not line up")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SUMMARY,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, CAPTURE), source)
        result = cs.key(f"{SOURCE}.result_set", "2024-06-18:county-board")
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
        inactive = record["inactive"]
        valid = record["ballots"] - inactive[0]
        values: list[tuple[UUID | None, int, int, int]] = [
            (None, 3, 0, record["ballots"]),
            (None, 5, 0, inactive[0]),
            (None, 4, 1, valid),
        ]
        for number, tally in enumerate(rounds, 1):
            values.append((None, 21, number, valid - sum(tally.values())))
            for label, votes in tally.items():
                values.append((options[label], 1, number, votes))
            for label in rounds[number - 2] if number > 1 else ():
                if label not in tally:
                    values.append((options[label], 1, number, 0))
        final = rounds[-1]
        winner = max(final, key=lambda label: final[label])
        for label, option in options.items():
            for outcome in ("won", "elected") if label == winner else ("lost" if label in final else "eliminated",):
                cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
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
