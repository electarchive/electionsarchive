import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.elector_districts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CERTIFICATE = "https://www.archives.gov/files/electoral-college/{year}/{prefix}ascertainment-{state}.pdf"
YEARS = {2012: "2012-", 2016: "", 2020: "", 2024: ""}
STATES = {"ME": "maine", "NE": "nebraska"}
DAYS = {2012: "2012-11-06", 2016: "2016-11-08", 2020: "2020-11-03", 2024: "2024-11-05"}
METHOD = (
    "Votes by congressional district for each ticket's district elector, from the state's certificate of "
    "ascertainment as the National Archives publishes it. The certificate lists only the tickets on the ballot, so "
    "write-ins, blanks, and scattering are not here; completeness is the tickets' share of the statewide vote."
)
MAINE = (
    "Maine counted {uocava} votes from uniformed-service and overseas voters only toward its at-large electors, not "
    "toward either district, so the two districts sum to slightly less than the statewide vote."
)


def url(year: int, state: str) -> str:
    return CERTIFICATE.format(year=year, prefix=YEARS[year], state=STATES[state])


def fetch() -> None:
    for year in YEARS:
        for state in STATES:
            raw.fetch(SOURCE, url(year, state), suffix=".pdf")


def parse() -> None:
    rows = []
    for year in YEARS:
        for state in STATES:
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year, state)).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            uocava = short = 0
            if state == "NE":
                for block in re.split(r"\n(?=\S[^\n]*,\s*Pre[^\n]*/)", text)[1:]:
                    numbers = [
                        int(re.sub(r"\D", "", found))
                        for found in re.findall(r"Vote\w*\s+Rec[^:\n]*:\s*(\d[\d ,]*)", block)
                    ][:4]
                    head = re.search(r"\s{2,}(.*?),\s*Pre", block.split("\n", 1)[0])
                    if len(numbers) != 4 or numbers[0] != sum(numbers[1:]) or head is None:
                        raise ValueError(f"{year} {state}: unreadable ticket {block[:80]!r} {numbers}")
                    rows.append((year, state, head.group(1), *numbers))
            else:
                found = re.search(r"There were ([\d,]+) votes", text)
                uocava = int(found.group(1).replace(",", "")) if found else 0
                for block in re.split(r"(?=The [A-Z ]+(?:PARTY|ALL)\s+Electors)", text)[1:]:
                    numbers = [
                        int(re.sub(r"\D", "", found.group(1)))
                        for line in block.split("\n")
                        if re.search(r"[a-z]", line)
                        and not re.search(r"District|Large|Congress|votes|There were|President", line, re.IGNORECASE)
                        and (found := re.search(r"\s(\d[\d ,]*\d|\d)\s*$", line))
                    ][:3]
                    head = re.search(r"candidacy\s*of\s*(.*?)\s*(?:/or|for)\s+President", block, re.DOTALL)
                    if len(numbers) != 3 or head is None or not 0 <= numbers[2] - numbers[0] - numbers[1] <= uocava:
                        raise ValueError(f"{year} {state}: unreadable ticket {block[:80]!r} {numbers}")
                    rows.append((year, state, re.sub(r"\s+", " ", head.group(1)), numbers[2], *numbers[:2], None))
                    short += numbers[2] - numbers[0] - numbers[1]
            if short > uocava:
                raise ValueError(f"{year} {state}: the districts fall short by more than the {uocava} overseas votes")
            rows.append((year, state, "", uocava, None, None, None))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int16,
            "state": pl.String,
            "candidate": pl.String,
            "statewide": pl.Int64,
            "d1": pl.Int64,
            "d2": pl.Int64,
            "d3": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "certificates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "certificates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's and Nebraska's presidential votes by congressional district 2012–2024, from the states' certificates of ascertainment",
            polity="US",
        )
        for (year, state), lines in frame.group_by("year", "state", maintain_order=True):
            day = DAYS[int(year)]
            uocava = int(lines.filter(pl.col("candidate") == "")["statewide"][0])
            lines = lines.filter(pl.col("candidate") != "")
            title = f"{year} certificate of ascertainment, {'Maine' if state == 'ME' else 'Nebraska'}"
            source = cs.source("National Archives and Records Administration", title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="National Archives and Records Administration",
                title=title,
                url=url(int(year), str(state)),
                license="Public domain",
            )
            cs.artifact(raw.cached(SOURCE, url(int(year), str(state))), source)
            options = conn.execute(
                """select o.id, o.kind, o.party_id, v.value::bigint,
                          (select array_agg(array[cy.role::text, cy.person_id::text, n.text] order by cy.role)
                           from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                           where cy.option_id = o.id)
                   from external_id x join contest_option o on o.contest_id = x.entity_id and o.kind in ('ticket', 'write_in')
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{day}:{state}:president"],
            ).fetchall()
            total = conn.execute(
                """select sum(v.value)::bigint from external_id x
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1 and v.option_k <> 0
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{day}:{state}:president"],
            ).fetchone()
            if total is None or total[0] is None:
                raise ValueError(f"{day}:{state}:president has no default votes")
            tickets = []
            for candidate, statewide, *districts in lines.select(
                "candidate", "statewide", "d1", "d2", "d3"
            ).iter_rows():
                surname = slug(candidate).split("-")[-1]
                found = [row for row in options if row[3] == statewide and row[4]] or [
                    row
                    for row in options
                    if any(role == "head" and slug(text).split("-")[-1] == surname for role, _, text in row[4] or [])
                ]
                if len(found) != 1:
                    raise ValueError(f"{day}:{state}: {candidate} ({statewide}) matches {len(found)} tickets")
                option, kind, party, held, candidacies = found[0]
                if abs(held - statewide) > max(5, held * 0.001):
                    raise ValueError(f"{day}:{state}: {candidate} {statewide} against {held} on file")
                tickets.append((kind, party, candidacies, [value for value in districts if value is not None]))
            counted = int(lines["statewide"].sum())
            for number in range(1, 3 if state == "ME" else 4):
                district_key = f"{day}:{state}-{number:02d}:president"
                contest, election, unit, existing = conn.execute(
                    """select c.id, c.election_id, u.id, rs.id from external_id x join contest c on c.id = x.entity_id
                       join reporting_unit u on u.area_id = c.district_area_id and u.scheme_code = 'us.cd'
                       join result_set rs on rs.contest_id = c.id and rs.is_default
                       where x.scheme = 'us.contest' and x.value = %s""",
                    [district_key],
                ).fetchone() or (None, None, None, None)
                if contest is None:
                    raise ValueError(f"{district_key} is not held")
                declared = conn.execute(
                    """select cy.person_id from outcome o join candidacy cy on cy.option_id = o.option_id
                       and cy.role = 'head' where o.result_set_id = %s and o.kind = 'won'""",
                    [existing],
                ).fetchone()
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                              supersedes_id from result_set where id = %s""",
                    [existing],
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
                                row,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", district_key)
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
                    completeness=round(counted / total[0], 4),
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
                if uocava:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=MAINE.format(uocava=f"{uocava:,}"))
                ranked = []
                for kind, party, candidacies, districts in tickets:
                    head = next(person for role, person, _ in candidacies if role == "head")
                    option_key = f"{district_key}:{head}"
                    option = cs.key("us.option", option_key)
                    if option in cs.minted:
                        cs.add("contest_option", id=option, contest_id=contest, kind=kind, party_id=party)
                        for role, person, text in candidacies:
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{option_key}:{role}"),
                                person_id=person,
                                option_id=option,
                                role=role,
                                ballot_name=text,
                                is_independent=False,
                            )
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        value=districts[number - 1],
                        flags=0,
                    )
                    ranked.append((districts[number - 1], head, option))
                ranked.sort(reverse=True)
                if declared is None or str(declared[0]) != ranked[0][1] or ranked[0][0] == ranked[1][0]:
                    raise ValueError(f"{district_key}: the certificate's leader is not the declared winner")
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][2], kind="won", seats=1)
        return cs.write()
