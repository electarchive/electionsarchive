import json
import re
import subprocess
import unicodedata
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.palm_desert_stv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Riverside County Registrar of Voters"
REPORT = "https://www.voteinfo.net/Elections/20221108/docs/RcvDetailedReport23.pdf"
CAPTURE = f"https://web.archive.org/web/20221202135311id_/{REPORT}"
TITLE = "RCV detailed report, Palm Desert City Council District 2, November 8, 2022 general election"
CONTEST = "2022 Palm Desert City Council election, Area 2"
SEATS = 2
METHOD = 'Data comes from the last ranked-choice report the county registrar posted, tabulated December 1, 2022. The report header still reads "Unofficial results." Two seats were filled by single transferable vote. In each round, a candidate at or above the threshold is elected and their surplus moves to the next continuing choices at a fractional value. Otherwise, the last-place candidate is eliminated and their ballots move at their current value. The threshold is recomputed each round as continuing votes divided by 3 plus 1, ignoring fractions. An elected candidate holds the threshold once their surplus moves. Blank and overvoted ballots in round 1 are invalid; exhausted counts include later overvotes and fractional remainders.'
DEMOTED = "Contains first-choice votes only from the state's election data archive; it is a few votes short of the registrar's final count. The registrar's round-by-round count is the default."


def fetch() -> None:
    raw.fetch(SOURCE, CAPTURE, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CAPTURE).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    if f"Number of positions to elect is {SEATS}." not in text or "Palm Desert City Council District 2" not in text:
        raise ValueError("unexpected report")
    rounds: list[dict[str, float]] = []
    thresholds: list[int] = []
    continuing: list[float] = []
    for block in re.split(r"(?m)^Round \d+$", text)[1:]:
        (threshold,) = re.findall(r"Threshold: ([\d,]+)\.0+", block)
        thresholds.append(int(threshold.replace(",", "")))
        figures: dict[str, str] = {}
        pending = last = ""
        for line in block[: block.index("Blanks")].splitlines():
            line = line.strip()
            if inline := re.fullmatch(r"(.+?)\s{2,}([\d,]+\.\d+)\s+[\d.]+%", line):
                last = inline.group(1)
                figures[last] = inline.group(2)
            elif named := re.fullmatch(r"(.+?)\s{2,}[\d.]+%", line):
                last = named.group(1)
                figures[last], pending = pending, ""
            elif line == "Continuing Ballots Total":
                last = line
                figures[last], pending = pending, ""
            elif re.fullmatch(r"[\d,]+\.\d+", line):
                pending = line
            elif re.fullmatch(r"\d+", line) and last:
                figures[last] += line
        values = {label: float(figure.replace(",", "")) for label, figure in figures.items()}
        continuing.append(values.pop("Continuing Ballots Total"))
        rounds.append(values)
    for number, tally in enumerate(rounds):
        if abs(sum(tally.values()) - continuing[number]) > 0.001:
            raise ValueError(f"round {number + 1} does not sum to its continuing ballots")
        if thresholds[number] != int(continuing[number] / (SEATS + 1)) + 1:
            raise ValueError(f"round {number + 1} threshold {thresholds[number]}")
    elected = list(dict.fromkeys(re.findall(r"(?m)^(.+?) is elected because", text)))
    (blanks,) = set(re.findall(r"(?m)^\s*([\d,]+\.\d+)\nBlanks", text))
    overvotes = re.findall(r"(?m)^Overvotes\s+([\d,]+\.\d+)", text)[0]
    invalid = round(float(blanks.replace(",", "")) + float(overvotes.replace(",", "")))
    if len(elected) != SEATS:
        raise ValueError(f"{len(elected)} elected for {SEATS} seats")
    record = {"ballots": round(continuing[0]) + invalid, "invalid": invalid, "elected": elected, "rounds": rounds}
    (PARSED / "official.json").write_text(json.dumps(record))


def emit() -> Path:
    record = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Palm Desert, California's 2022 two-seat District 2 council election, round by round from the "
            "Riverside County registrar's final RCV report, restated as STV",
            polity="US",
        )

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        ((contest, election, polity),) = conn.execute(
            """select k.id, k.election_id, e.polity_id from contest k join name n on n.entity_id = k.id
               join election e on e.id = k.election_id where n.text = %s""",
            [CONTEST],
        ).fetchall()
        top = cs.unit("us.local_district", "06:0655184:council:area-2")
        if top in cs.minted:
            raise ValueError("Palm Desert's Area 2 reporting unit must already exist")
        stv = cs.key("us.electoral_system", "palm-desert:stv")
        cs.add(
            "electoral_system", id=stv, polity_id=polity, rules={"formula": "stv", "quota": "droop"}, rules_version=1
        )
        cs.add(
            "name",
            entity_id=stv,
            text="Palm Desert single transferable vote",
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
        (row,) = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchall()
        cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=stv)
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
            url=REPORT,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, CAPTURE), source)
        result = cs.key(f"{SOURCE}.result_set", "2022-11-08:council:area-2")
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
        valid = record["ballots"] - record["invalid"]
        values: list[tuple[UUID | None, int, int, float]] = [
            (None, 3, 0, record["ballots"]),
            (None, 5, 0, record["invalid"]),
            (None, 4, 1, valid),
        ]
        for number, tally in enumerate(rounds, 1):
            values.append((None, 21, number, round(valid - sum(tally.values()), 6)))
            for label, votes in tally.items():
                if number == 1 or rounds[number - 2][label]:
                    values.append((options[label], 1, number, votes))
        final = rounds[-1]
        for label, option in options.items():
            for outcome in (
                ("won", "elected") if label in record["elected"] else ("lost" if final[label] else "eliminated",)
            ):
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
                flags=1 if measure in (3, 4, 21) else 0,
                value=votes,
            )
        return cs.write()
