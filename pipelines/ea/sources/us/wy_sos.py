import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.wy_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://sos.wyo.gov/Elections/Docs/"
PUBLISHER = "Wyoming Secretary of State"
TITLE = "Statewide issues abstracts"
FILES = {
    date(1996, 11, 5): "1996/96Results/96General/96General_SW_Candidate_Judicial_Const.Amendments.pdf",
    date(1998, 11, 3): "1998/98Results/98General/Statewide_Issues%20Abstract.pdf",
}
PEOPLE = {"Dawson": ("Dave Dawson", "libertarian-party", ":WY:house:0$")}
OFFICES = {
    "United States President and Vice President": "president",
    "United States Senate": "senate",
    "Governor": "governor",
}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '56%'"""
            ).fetchall()
        }
    if len(codes) != 23:
        raise ValueError("Wyoming needs 23 counties")
    rows: list[dict[str, object]] = []
    for day, path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for page in text.split("\f"):
            lines = [line.strip() for line in page.splitlines() if line.strip()]
            office = next((OFFICES[line] for line in lines if line in OFFICES), None)
            if office is None:
                continue
            first = next(i for i, line in enumerate(lines) if re.match(r"Albany\s", line))
            names = (
                re.findall(r"([A-Z][A-Za-z'-]+) &", lines[first - 2])
                if office == "president"
                else re.findall(r"([A-Z][A-Za-z'-]+) \(", lines[first - 1])
            )
            body = lines[first : first + 23]
            (total,) = [line for line in lines if line.startswith("Official Totals")]
            printed = [int(value.replace(",", "")) for value in re.findall(r"[\d,]+", total)]
            values: list[list[int]] = []
            keys: list[str] = []
            for line in body:
                found = re.fullmatch(r"([A-Za-z ]+?)\s{2,}([\d,\s]+)", line)
                if found is None:
                    raise ValueError(f"{day} {office}: {line}")
                keys.append(re.sub(r"[^a-z]", "", found[1].lower()))
                values.append([int(value.replace(",", "")) for value in found[2].split()])
            if sorted(keys) != sorted(codes):
                raise ValueError(f"{day} {office}: counties {sorted(set(keys) ^ set(codes))}")
            skip = len(printed) - len(names)
            for column, name in enumerate(names, skip):
                column_values = [row[column] for row in values]
                if sum(column_values) != printed[column]:
                    raise ValueError(f"{day} {office} {name}: {sum(column_values)}, printed {printed[column]}")
                rows.extend(
                    {"day": day, "office": office, "candidate": name, "county": codes[key], "votes": value}
                    for key, value in zip(keys, column_values, strict=True)
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wyoming's president and U.S. Senate 1996 and governor 1998 by county "
            "from the Secretary of State's statewide abstracts",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.wyo.gov/Elections/State/ElectionResults.aspx",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '56%%'"
            ).fetchall()
        )
        columns = (
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
        )
        for (day, office), lines in frame.sort("day").group_by("day", "office", maintain_order=True):
            key = f"{day}:WY:{office}"
            ((contest, election),) = conn.execute(
                """select c.id, c.election_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            options = conn.execute(
                """select id, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
                [contest],
            ).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", key)
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            picked: dict[str, UUID] = {}
            for candidate, votes in (
                lines.group_by("candidate", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [
                    option
                    for option, text in options
                    if re.sub(r"[^a-z]", "", (text or "").replace(" Jr.", "").split()[-1].lower()) == candidate.lower()
                ]
                if not matches and candidate in PEOPLE:
                    shown, party, ran = PEOPLE[candidate]
                    ((person,),) = conn.execute(
                        """select distinct cd.person_id from candidacy cd
                           join contest_option o on o.id = cd.option_id
                           join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                                and x.value ~ %s
                           join name n on n.entity_id = cd.person_id and n.is_preferred and n.text = %s""",
                        [ran, shown],
                    ).fetchall()
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party),
                        ballot_label=shown,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=shown,
                    )
                    matches = [option]
                if len(matches) != 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches[0] in on_file and abs(on_file[matches[0]] - votes) > max(10, on_file[matches[0]] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[matches[0]]} on file")
                picked[candidate] = matches[0]
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=picked[candidate],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
