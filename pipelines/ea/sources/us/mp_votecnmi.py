import html
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.mp_votecnmi"
VERSION = "2"
PUBLISHER = "Commonwealth Election Commission of the Northern Mariana Islands"
YEARS = {
    2014: date(2014, 11, 4),
    2016: date(2016, 11, 8),
    2018: date(2018, 11, 6),
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2024: date(2024, 11, 5),
}
MUNICIPIO = {"ED6": "69120", "ED7": "69100", "ED4D": "69085"}
HEADINGS = ("DELEGATE TO", "GOVERNOR", "SENAT", "HOUSE OF", "MAYOR", "ATTORNEY", "BOARD OF", "COUNCIL", "JUSTICE", "INITIATIVE", "ABSENTEE")


def url(year: int) -> str:
    return f"https://www.votecnmi.gov.mp/election-results-archive/{year}-election-results"


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, url(year))
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url(year)], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url(year), body, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Mariana Islands governor and delegate elections 2014-2024 by election district, with early and absentee voting",
            polity="US-MP",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-MP'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        municipios = {
            code: (unit, unit_area)
            for code, unit, unit_area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and polity_id = %s", [state]
            ).fetchall()
        }
        office = cs.key("us.office", "governor:MP")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=state, kind="governor")
            cs.add("name", entity_id=office, text="Governor of the Northern Mariana Islands", lang="en", kind="official", is_preferred=True)
        two_round = cs.key("us.electoral_system", "two_round")
        persons = {}
        for year, day in YEARS.items():
            title = f"{year} Election Results"
            source = cs.source(PUBLISHER, title)
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=url(year), license="Public record")
            cs.artifact(raw.cached(SOURCE, url(year)), source)
            page = raw.cached(SOURCE, url(year)).path.read_text(encoding="utf-8", errors="replace")
            tables = {}
            units = {}
            for found in re.finditer(r"<tr[^>]*>(?:(?!</tr>).)*?CANDIDATE/AFFIL", page, flags=re.S):
                before = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page[max(0, found.start() - 1500) : found.start()]))).upper()
                kind = max((before.rfind(heading), heading) for heading in HEADINGS)[1]
                if kind in ("DELEGATE TO", "GOVERNOR") and kind not in tables:
                    tables[kind] = found.start()
            for kind, position in tables.items():
                grid = []
                for row in re.findall(r"<tr.*?</tr>", page[position:], flags=re.S):
                    cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
                    if cells:
                        grid.append(cells)
                    if cells and cells[0].upper() == "TOTAL":
                        break
                header, *body = grid
                body = body[:-1]
                columns = [
                    re.match(r"ED\d[A-D]?", text.upper())[0]
                    if text.upper().startswith("ED")
                    else re.sub(r"\s+", "-", text.upper())
                    for text in header[1:]
                ]
                governor = kind == "GOVERNOR"
                contest_key = f"{day}:MP:governor" if governor else f"{day}:MP:house:0"
                election = conn.execute(
                    "select c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                    [f"{day}:MP:house:0"],
                ).fetchone()[0]
                if governor:
                    contest = cs.key("us.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office",
                        office_id=office,
                        district_area_id=area,
                        term="full",
                        magnitude=1,
                        round_number=1,
                        electoral_system_id=two_round,
                        default_result_kind="counted",
                    )
                    cs.add("name", entity_id=contest, text=f"{year} Northern Mariana Islands governor election", lang="en", kind="official", is_preferred=True)
                    cs.add("slug", path=f"us/mp/{day}-general/governor", entity_id=contest)
                    options = []
                    for row in body:
                        ticket = re.sub(r"/[A-Za-z. ]+$", "", row[0]).replace("*", "")
                        names = [part.strip() for part in re.split(r"\s+&\s+|/", ticket) if part.strip()]
                        people = [" ".join(reversed([piece.strip() for piece in name.split(",", 1)])).title() for name in names]
                        token = slug(" ".join(people))
                        option = cs.key("us.option", f"{contest_key}:{token}")
                        cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=" and ".join(people))
                        for role, label in zip(("head", "running_mate"), people, strict=False):
                            person = persons.setdefault(slug(label), cs.key(f"{SOURCE}.person", f"MP:{slug(label)}"))
                            if person in cs.minted and not any(r["id"] == person for r in cs.rows.get("person", [])):
                                cs.add("person", id=person)
                                cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{contest_key}:{token}:{role}"),
                                person_id=person,
                                option_id=option,
                                role=role,
                                ballot_name=label if role == "head" else None,
                                is_independent=False,
                            )
                        options.append(option)
                else:
                    contest = conn.execute(
                        "select c.id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                        [contest_key],
                    ).fetchone()[0]
                    labels = conn.execute("select id, ballot_label from contest_option where contest_id = %s", [contest]).fetchall()
                    options = []
                    for row in body:
                        surname, given = (part.strip().lower() for part in row[0].split("/")[0].replace("*", "").split(",", 1))
                        found = [
                            option
                            for option, label in labels
                            if surname in label.lower() and given.split()[0] in label.lower()
                        ]
                        if len(found) != 1:
                            raise ValueError(f"{year}: {len(found)} options for {row[0]}")
                        options.append(found[0])
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:district")
                counted = sum(int(row[1 + columns.index("TOTAL")].replace(",", "")) for row in body)
                if governor:
                    completeness = 1
                else:
                    columns_set = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
                    for prior in conn.execute(
                        f"select {', '.join(columns_set)} from result_set where contest_id = %s and is_default and id <> %s",
                        [contest, result_set],
                    ).fetchall():
                        cs.add("result_set", **dict(zip(columns_set, prior, strict=True)), is_default=False)
                    for option, outcome_kind, seats in conn.execute(
                        """select o.option_id, o.kind, o.seats from outcome o
                           join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                        [contest, result_set],
                    ).fetchall():
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
                    official = conn.execute(
                        """select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k
                           where r.contest_id = %s and r.is_default and v.measure = 1""",
                        [contest],
                    ).fetchone()[0]
                    completeness = round(float(counted) / float(official), 4)
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
                    completeness=completeness,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="Votes by election district as published; early voting and absentee votes are separate aggregate units on their own channels where the page gives them separate columns.",
                )
                if governor:
                    ranked = sorted(zip(options, (int(row[1 + columns.index("TOTAL")].replace(",", "")) for row in body), strict=True), key=lambda pair: -pair[1])
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0][0], kind="elected", seats=None)
                has_early = any(name.startswith("EV") for name in columns)
                for row, option in zip(body, options, strict=True):
                    for name, cell in zip(columns, row[1:], strict=False):
                        if name == "TOTAL" or not cell.replace(",", "").isdigit():
                            continue
                        aggregate = name.startswith(("EV", "ABS"))
                        code = f"MP:{year}:{name}" if aggregate else f"{year}:MP:{name}"
                        scheme = "us.aggregate" if aggregate else "us.precinct"
                        channel = (2 if name.startswith("EV") else 3) if aggregate else (1 if has_early else 0)
                        if code not in units:
                            units[code] = cs.unit(scheme, code)
                            municipio = None if aggregate else municipios[MUNICIPIO.get(name, "69110")]
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=state,
                                scheme_code=scheme,
                                code=code,
                                parent_id=municipio[0] if municipio else parent,
                                area_relation="within",
                                area_id=municipio[1] if municipio else area,
                                kind="aggregate" if aggregate else "standard",
                                valid_from=day,
                                valid_to=day + timedelta(days=1),
                            )
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=units[code],
                            option_id=option,
                            channel=channel,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=int(cell.replace(",", "")),
                        )
        return cs.write()
