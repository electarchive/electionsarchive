import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.electionsfl import FIPS
from ea.store import connect

SOURCE = "us.fl_primary_2026"
VERSION = "1"
DAY = date(2026, 8, 18)
ENR = "https://enr.electionsfl.org/{county}/{election}/Summary/"
ELECTIONS = {
    "BAY": [4001],
    "ALA": [4023],
    "BAK": [4047],
    "BRE": [3995],
    "BRA": [4017],
    "CIT": [3991],
    "CHA": [4016],
    "CLA": [3990],
    "CLM": [4020],
    "CLL": [4039],
    "DES": [3997],
    "DUV": [4003, 4053],
    "FLA": [4029],
    "FRA": [4028],
    "GAD": [4009],
    "HAM": [4019],
    "GUL": [4044],
    "HIG": [4037],
    "HER": [4046],
    "HIL": [4002],
    "IND": [4032],
    "HOL": [4040],
    "LAF": [4033],
    "JEF": [4043],
    "JAC": [4048],
    "LAK": [3996],
    "LEO": [3994],
    "LEE": [4025],
    "LEV": [3992, 4058],
    "MAD": [4008],
    "LIB": [4049],
    "MRN": [4007],
    "MAN": [4031],
    "DAD": [4010],
    "MON": [4006],
    "NAS": [4021],
    "ORA": [3998],
    "OKA": [4026],
    "OKE": [4060],
    "PAL": [4035],
    "PAS": [4045],
    "POL": [3999, 4000],
    "PUT": [4013],
    "STJ": [4011],
    "STL": [4030],
    "SAN": [4036],
    "SAR": [4038],
    "SUM": [4056],
    "TAY": [4018],
    "SUW": [4050],
    "VOL": [4004],
    "WAL": [4027],
    "WAS": [4034],
}
ROLES = (
    (r"^(?:board of county commissioners|county commissioner)\b", "commission"),
    (r"^school board(?: member)?\b", "board-of-education"),
)
PARTIES = {"REP": "republican-party", "DEM": "democratic-party"}
OFFICES = {"commission": "County Commission", "board-of-education": "School Board"}


def fetch() -> None:
    for county, numbers in ELECTIONS.items():
        for number in numbers:
            raw.fetch(SOURCE, ENR.format(county=county, election=number), suffix=".html")


def races(county: str) -> list[tuple[str, str, str, bool, list[tuple[str, str, int]]]]:
    found = []
    for number in ELECTIONS[county]:
        url = ENR.format(county=county, election=number)
        page = raw.cached(SOURCE, url).path.read_text(errors="replace")
        heading = re.search(r"Election Date:\s*([\d/]+)", page)
        title = re.search(r"<h1>([^<]*)</h1>", page)
        if heading is None or heading[1] != "8/18/2026" or title is None or "test" in title[1].lower():
            continue
        for block in re.split(r'<div id="\d+" class="Race row">', page)[1:]:
            label = re.search(r"<label>([^<]*)</label>", block)
            district = re.search(r"\bdistrict\s*(\d+)", label[1], re.IGNORECASE) if label else None
            if label is None or district is None:
                continue
            name = html.unescape(label[1]).strip()
            role = next((role for pattern, role in ROLES if re.search(pattern, name, re.IGNORECASE)), None)
            if role is None:
                continue
            universal = "universal" in name.lower()
            candidates = []
            for cell, votes in re.findall(
                r'<td class="ChoiceColumn">(.*?)<div class="DetailsFullBar">.*?class="DetailResultsColumn notranslate TotalVotes">([\d,]+)<',
                block,
                re.DOTALL,
            ):
                text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                match = re.match(r"^(.*?)\s*\(([A-Z]{3})\)$", text)
                candidates.append((match[1] if match else text, match[2] if match else "", int(votes.replace(",", ""))))
            if candidates:
                found.append((url, f"{role}:district-{district[1]}", name, universal, candidates))
    return found


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida county commission and school board contests from the 2026-08-18 primary, from each "
            "county Supervisor of Elections' official election-night reporting site",
            polity="US",
        )
        parent = cs.key("us.election", f"{DAY}:FL")
        if parent in cs.minted:
            raise ValueError("the 2026-08-18 Florida local election must exist")
        values: list[tuple[str, str, str, int]] = []
        counts = {"contests": 0, "held": 0, "no template": 0, "slug taken": 0, "decided": 0, "people": 0}
        county_units = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '12%%'"
            ).fetchall()
        }
        elections: dict[str, object] = {}
        for county in ELECTIONS:
            fips = str(FIPS[county])
            rows = races(county)
            if not rows:
                continue
            title = f"{county} County Supervisor of Elections, 2026 Primary Election official results"
            source = cs.source(f"{county} County Supervisor of Elections", title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=f"{county} County Supervisor of Elections",
                    title=title,
                    url=rows[0][0],
                    license="Public record",
                )
            for url in {url for url, *_ in rows}:
                cs.artifact(raw.cached(SOURCE, url), source)
            for _, tail, label, universal, candidates in rows:
                _role, seat = tail.split(":")
                parties = {party for _, party, _ in candidates}
                if not universal and len(parties - {""}) > 1:
                    continue
                party_slug = (
                    None if universal or not (parties & set(PARTIES)) else PARTIES[next(iter(parties & set(PARTIES)))]
                )
                full = f"FL:{fips}:{tail}"
                value = f"{DAY}:{full}" + (f":{party_slug.removesuffix('-party')}" if party_slug else "")
                if conn.execute(
                    "select 1 from external_id where scheme = 'us.contest' and value = %s", [value]
                ).fetchone():
                    counts["held"] += 1
                    continue
                template = conn.execute(
                    """select co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id, co.boundary_set_id,
                              co.magnitude, co.term, co.electoral_system_id, el.id, el.polity_id, n.text, s.path
                       from external_id x join contest co on co.id = x.entity_id
                       join entity e on e.id = co.id and e.retired_by is null
                       join election el on el.id = co.election_id
                       left join name n on n.entity_id = co.id and n.is_preferred and n.lang = 'en'
                       left join slug s on s.entity_id = co.id and s.is_current
                       where x.scheme = 'us.contest' and regexp_replace(x.value, '^[0-9-]+:', '') = %s
                           and (co.office_id is not null or co.body_id is not null) and co.district_area_id is not null
                       order by el.date_from desc limit 1""",
                    [full],
                ).fetchone()
                if template is None:
                    counts["no template"] += 1
                    print("no template", full)
                    continue
                kind, office, body, area, seat, plan, magnitude, term, system, template_election, polity, name, path = (
                    template
                )
                election_key = f"{DAY}:FL:{fips}"
                if election_key not in elections:
                    found = conn.execute(
                        "select entity_id from external_id where scheme = 'us.election' and value = %s", [election_key]
                    ).fetchone()
                    election = found[0] if found else cs.key("us.election", election_key)
                    if election in cs.minted:
                        base = conn.execute(
                            """select n.text, s.path from name n left join slug s on s.entity_id = n.entity_id and s.is_current
                               where n.entity_id = %s and n.is_preferred and n.lang = 'en'""",
                            [template_election],
                        ).fetchone()
                        cs.add(
                            "election",
                            id=election,
                            polity_id=polity,
                            parent_id=parent,
                            kind="general",
                            date_from=DAY,
                            date_to=DAY,
                            date_precision="day",
                            status="held",
                        )
                        cs.add(
                            "name",
                            entity_id=election,
                            text=re.sub(r"^\d{4}", "2026", base[0]),
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                        cs.add("slug", path=re.sub(r"\d{4}-\d{2}-\d{2}", str(DAY), base[1]), entity_id=election)
                    elections[election_key] = election
                election = elections[election_key]
                contest_path = re.sub(r"\d{4}-\d{2}-\d{2}-\w+", f"{DAY}-general", path or "") + (
                    f"-{party_slug.removesuffix('-party')}" if party_slug else ""
                )
                if conn.execute("select 1 from slug where path = %s", [contest_path]).fetchone():
                    counts["slug taken"] += 1
                    continue
                contest = cs.key("us.contest", value)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind=kind,
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    seat_id=seat,
                    boundary_set_id=plan,
                    magnitude=magnitude,
                    term=term,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                text = re.sub(r"^\d{4}", "2026", name or "") + (
                    f" ({party_slug.removesuffix('-party').title()} primary)" if party_slug else ""
                )
                cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=contest_path, entity_id=contest)
                counts["contests"] += 1
                known: dict[str, object] = {}
                for person, ballot in conn.execute(
                    """select ca.person_id, coalesce(ca.ballot_name, o.ballot_label) from contest co
                       join contest_option o on o.contest_id = co.id
                       join candidacy ca on ca.option_id = o.id
                       where co.district_area_id = %s and co.id <> %s""",
                    [area, contest],
                ).fetchall():
                    words = slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", ballot or "")).split("-")
                    known.setdefault(f"{words[0][:1]}:{words[-1]}", person)
                result_set = cs.key(f"{SOURCE}.result_set", value)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                total = sum(votes for _, _, votes in candidates)
                ranked = sorted(candidates, key=lambda row: -row[2])
                decided = (universal or party_slug is None) and (len(ranked) == 1 or ranked[0][2] * 2 > total)
                counts["decided"] += decided
                for rank, (candidate, party, votes) in enumerate(ranked):
                    words = slug(re.sub(r'"[^"]*"|\([^)]*\)', " ", candidate)).split("-")
                    person = known.get(f"{words[0][:1]}:{words[-1]}")
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"{value}:{slug(candidate)}")
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
                        counts["people"] += 1
                    option = cs.key("us.option", f"{value}:{slug(candidate)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", PARTIES[party]) if party in PARTIES else None,
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{value}:{slug(candidate)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party == "NPA",
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind=("elected" if decided else "advanced")
                        if rank < (1 if decided or party_slug else 2)
                        else "lost",
                    )
                    values.append((str(result_set), str(county_units[fips][0]), str(option), votes))
        print(counts)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema=["result_set_id", "unit_id", "option_id", "value"], orient="row").with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
