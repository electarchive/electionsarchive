import html
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.cary_irv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wake County Board of Elections"
FOLDER = (
    "http://www.wakegov.com/elections/data/Past%20Election%20Results/2007-10-09%20-%20Raleigh%20and%20Cary%20Municipal"
    "%20and%20Board%20of%20Education%20Election%2C%20and%20Wake%20County%20and%20Raleigh%20Bond%20Referendums/"
)
WAYBACK = "https://web.archive.org/web/{}id_/{}"
DAY = date(2007, 10, 9)
FILES = {
    "canvass": ("20201018053816", "20071009.Canvass.htm", "Official numbered key canvass, October 9, 2007"),
    "irv": ("20201018065211", "20071009.IRV.htm", "Instant runoff results for Cary Town Council District B"),
}
CONTEST = "alged:3710740_2007_10_cary_NC_City Council_b_1"
HEADING = "COUNCIL MEMBER C-B 1 CARY MUNICIPAL DISTRICT B"
METHOD = "Round 1 data comes from the Wake County Board of Elections' official canvass. Round 2 data comes from the board's instant runoff sheet, which keeps the top two candidates and adds the second and third choices from ballots for the others. Ballots without a later choice for either finalist are counted as exhausted. The sheet gives Don Frantz 352 first-choice votes in precinct 04-02, while the canvass gives 353."
DEMOTED = "Lists first-choice totals compiled by the American Local Government Elections Database. The county's official round-by-round count is the default."


def url(name: str) -> str:
    stamp, path, _ = FILES[name]
    return WAYBACK.format(stamp, FOLDER + path)


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=".htm")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    text = raw.cached(SOURCE, url("canvass")).path.read_text(encoding="cp1252")
    lines = [
        html.unescape(re.sub(r"<[^>]+>", "", block)).replace("\xa0", " ") for block in re.split(r"(?i)</pre>", text)
    ]
    start = next(number for number, line in enumerate(lines) if HEADING in line)
    names: dict[str, str] = {}
    totals: dict[str, int] = {}
    units: dict[str, list[int]] = {}
    for line in lines[start:]:
        if line.startswith("====="):
            break
        for code, label, votes in re.findall(r"(\d\d) = (.+?)\s{2,}([\d,]+)\s+[\d.]+", line):
            names[code] = label.strip()
            totals[code] = int(votes.replace(",", ""))
        row = re.fullmatch(r"\d{4} (.+?)\s{2,}([\d\s]+)", line.strip())
        if row is not None:
            units[row[1].strip()] = [int(cell) for cell in row[2].split()]
    codes = sorted(names)
    first = {names[code]: totals[code] for code in codes}
    for number, code in enumerate(codes):
        if sum(values[number] for values in units.values()) != totals[code]:
            raise ValueError(f"canvass: the precincts do not add up for {names[code]}")

    sheet = raw.cached(SOURCE, url("irv")).path.read_text(encoding="cp1252")
    second: dict[str, int] = {}
    columns: dict[str, list[int]] = {}
    for row in re.findall(r"(?is)<tr.*?</tr>", sheet):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)
        ]
        cells = [cell for cell in cells if cell]
        numbers = [cell for cell in cells if re.fullmatch(r"\d+", cell)]
        label = next((cell for cell in cells if cell in first), None)
        if label is None or len(numbers) < 4:
            continue
        values = [int(cell) for cell in numbers[:4]]
        if sum(values[:3]) != values[3]:
            raise ValueError(f"irv: a row for {label} does not add up")
        columns.setdefault(label, [0, 0, 0])
        for number in range(3):
            columns[label][number] += values[number]
        if len(numbers) > 4:
            second[label] = int(numbers[4])
    for label, values in columns.items():
        if sum(values) != second[label]:
            raise ValueError(f"irv: the precincts do not add up for {label}")
    if sum(second.values()) != int(re.search(r"Total\s*</td>.*?=\s*</td>.*?>(\d+)<", sheet, re.DOTALL)[1]):
        raise ValueError("irv: the finalists do not add up to the total")
    (PARSED / "contest.json").write_text(json.dumps({"first": first, "second": second, "columns": columns}))


def emit() -> Path:
    record = json.loads((PARSED / "contest.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cary, North Carolina's 2007 instant runoff for Town Council District B, both rounds from the Wake "
            "County Board of Elections' official canvass and runoff sheet",
            polity="US",
        )
        ((contest, election, polity),) = conn.execute(
            """select k.id, k.election_id, e.polity_id from external_id x join contest k on k.id = x.entity_id
               join election e on e.id = k.election_id where x.scheme = 'us.contest' and x.value = %s""",
            [CONTEST],
        ).fetchall()
        irv = cs.key("us.electoral_system", "cary-nc:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=polity,
            rules={"formula": "irv", "maxRankings": 3, "batchElimination": True},
            rules_version=1,
        )
        cs.add("name", entity_id=irv, text="Cary instant runoff voting", lang="en", kind="official", is_preferred=True)
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
        ((top,),) = conn.execute(
            """select distinct u.id from result_set r join public.result_value v on v.result_set_k = r.k
               join reporting_unit u on u.k = v.unit_k where r.contest_id = %s""",
            [contest],
        ).fetchall()
        fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness")
        fields += ("supersedes_id",)
        for values in conn.execute(
            f"""select {", ".join(fields)} from result_set where contest_id = %s
                and changeset_id not in (select id from changeset where pipeline = %s)""",
            [contest, SOURCE],
        ).fetchall():
            cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
            cs.add("note", entity_id=values[0], kind="caveat", body_md=DEMOTED)
        options: dict[str, UUID] = dict(
            conn.execute(
                "select ballot_label, id from contest_option where contest_id = %s and kind = 'candidate'", [contest]
            ).fetchall()
        )

        result = cs.key(f"{SOURCE}.result_set", f"{DAY}:district-b")
        sources = []
        for name, (_, path, title) in FILES.items():
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=FOLDER + path,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url(name)), source)
            sources.append(source)
        cs.add(
            "result_set",
            id=result,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=sources[0],
            is_default=True,
            completeness=1,
        )
        cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
        first, second = record["first"], record["second"]
        valid = sum(first.values())
        winner = max(second, key=lambda label: second[label])
        values: list[tuple[UUID | None, int, int, int]] = [
            (None, 4, 0, valid),
            (None, 21, 1, 0),
            (None, 21, 2, valid - sum(second.values())),
        ]
        for label, votes in first.items():
            if label == "WRITE-IN":
                option = cs.key("us.option", f"{contest}:write_in:scattered")
                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                cs.add("outcome", result_set_id=result, option_id=option, kind="eliminated")
            else:
                option = options[label]
                for outcome in (
                    ("won", "elected") if label == winner else ("lost" if label in second else "eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            values.append((option, 1, 1, votes))
            if label in second:
                values.append((option, 1, 2, second[label]))
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
