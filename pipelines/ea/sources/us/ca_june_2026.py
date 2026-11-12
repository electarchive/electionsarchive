import csv
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.ca_june_2026"
VERSION = "1"
DAY = date(2026, 6, 2)
ROWS = Path(__file__).with_name("ca_june_2026.tsv")
TITLE = "June 2, 2026 Statewide Direct Primary Election, official results"
PUBLISHERS = {
    "06017": "El Dorado County Elections Department",
    "06019": "Fresno County Clerk/Registrar of Voters",
    "06025": "Imperial County Registrar of Voters",
    "06039": "Madera County Elections Division",
    "06053": "Monterey County Elections Department",
    "06059": "Orange County Registrar of Voters",
    "06067": "Sacramento County Voter Registration and Elections",
    "06071": "San Bernardino County Registrar of Voters",
    "06073": "San Diego County Registrar of Voters",
    "06085": "Santa Clara County Registrar of Voters",
    "06089": "Shasta County Clerk/Registrar of Voters",
    "06095": "Solano County Registrar of Voters",
    "06097": "Sonoma County Registrar of Voters",
    "06107": "Tulare County Registrar of Voters",
    "06111": "Ventura County Clerk-Recorder, Registrar of Voters",
}
NOTE = (
    "Candidate totals from the county's official results for the June 2, 2026 primary. The top two advanced to the "
    "November 3, 2026 runoff, as that contest's ballot shows."
)


def _rows() -> list[dict[str, str]]:
    with ROWS.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fetch() -> None:
    for url in sorted({row["url"] for row in _rows()}):
        raw.fetch(
            SOURCE,
            url,
            suffix=".pdf" if url.endswith(".pdf") else ".json" if "json" in url or "/api/" in url else ".html",
        )


def _words(text: str) -> list[str]:
    return [
        word
        for word in re.sub(r'"[^"]*"|\([^)]*\)', " ", text.lower()).replace(".", " ").split()
        if word not in ("jr", "sr", "ii", "iii", "iv") and len(word) > 1
    ]


def emit() -> Path:
    contests: dict[str, list[dict[str, str]]] = {}
    for row in _rows():
        contests.setdefault(row["tail"], []).append(row)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California June 2, 2026 local primaries whose top two met in the November 3 runoff",
            polity="US-CA",
        )
        system = cs.key("us.electoral_system", "two_round")
        parent = cs.key("us.election", f"{DAY}:CA:primary")
        if {system, parent} & cs.minted:
            raise ValueError("the two-round system and the California primary election must already exist")
        sources = {}
        for fips, publisher in PUBLISHERS.items():
            urls = sorted({row["url"] for row in _rows() if row["county"] == fips})
            if not urls:
                continue
            source = sources[fips] = cs.source(publisher, TITLE)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=TITLE,
                url=urls[0],
                license="Public record",
            )
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), source)
        added: set[object] = set()
        values = []
        for tail, rows in contests.items():
            found = conn.execute(
                """select co.id, co.office_id, co.body_id, co.district_area_id, co.seat_id, co.boundary_set_id,
                          co.magnitude, co.term, el.polity_id, pn.text, n.text, s.path, es.path, ex.value
                   from external_id x join contest co on co.id = x.entity_id
                   join entity e on e.id = co.id and e.retired_by is null
                   join election el on el.id = co.election_id
                   join external_id ex on ex.entity_id = el.id and ex.scheme = 'us.election'
                   join name n on n.entity_id = co.id and n.is_preferred and n.lang = 'en'
                   join name pn on pn.entity_id = el.polity_id and pn.is_preferred and pn.lang = 'en'
                   join slug s on s.entity_id = co.id and s.is_current
                   join slug es on es.entity_id = el.id and es.is_current
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"2026-11-03:{tail}"],
            ).fetchone()
            if found is None:
                raise ValueError(f"no November 3 contest for {tail}")
            (nov, office, body, area, seat, plan, magnitude, term, polity, place, title, path, election_path, key) = (
                found
            )
            code = key.split(":", 2)[2]
            election = cs.key("us.election", f"{DAY}:CA:{code}:primary")
            home = election_path.rsplit("/", 1)[0]
            if election in cs.minted and election not in added:
                added.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="primary",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} primary election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{home}/{DAY}-primary", entity_id=election)
            contest = cs.key("us.contest", f"{DAY}:{tail}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
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
            cs.add(
                "name",
                entity_id=contest,
                text=re.sub(r" (?:runoff )?election", " primary election", title, count=1),
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"{home}/{DAY}-primary/{path.rsplit('/', 1)[1]}", entity_id=contest)
            source = sources[rows[0]["county"]]
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", f"{DAY}:{tail}")
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            people: dict[tuple[str, str], object] = {}
            for person, text in conn.execute(
                """select distinct ca.person_id, coalesce(ca.ballot_name, pn.text)
                   from candidacy ca join contest_option o on o.id = ca.option_id
                   join contest co on co.id = o.contest_id join election el on el.id = co.election_id
                   join entity e on e.id = ca.id and e.retired_by is null
                   left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
                   where (el.polity_id = %s or co.id = %s) and ca.person_id is not null""",
                [polity, nov],
            ).fetchall():
                parts = _words(text or "")
                if len(parts) >= 2:
                    people.setdefault((parts[0], parts[-1]), person)
            finalists = {
                (parts[0], parts[-1])
                for (text,) in conn.execute(
                    """select ca.ballot_name from candidacy ca join contest_option o on o.id = ca.option_id
                       join entity e on e.id = ca.id and e.retired_by is null where o.contest_id = %s""",
                    [nov],
                ).fetchall()
                if len(parts := _words(text or "")) >= 2
            }
            ranked = sorted(rows, key=lambda row: -int(row["votes"]))
            if len(ranked) > 2 and int(ranked[1]["votes"]) == int(ranked[2]["votes"]):
                raise ValueError(f"{tail}: the top two are not clear runoff finalists")
            top = {(_words(row["candidate"])[0], _words(row["candidate"])[-1]) for row in ranked[:2]}
            if top != finalists:
                raise ValueError(
                    f"{tail}: the top two {sorted(top)} are not the November finalists {sorted(finalists)}"
                )
            district = conn.execute(
                """select a.scheme_code, coalesce(u.code, c.code) from area a join area_code c on c.area_id = a.id
                   left join reporting_unit u on u.area_id = a.id and u.area_relation = 'equals'
                   where a.id = %s order by u.code is null, c.code limit 1""",
                [area],
            ).fetchone()
            if district is None:
                raise ValueError(f"{tail}: the district has no code")
            scheme, area_code = district
            unit = cs.unit(scheme, area_code)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=area_code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            for position, row in enumerate(ranked, start=1):
                label = row["candidate"]
                parts = _words(label)
                option = cs.key("us.option", f"{DAY}:{tail}:{position}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=label,
                )
                person = people.get((parts[0], parts[-1])) or cs.key(
                    f"{SOURCE}.person", f"{DAY}:{tail}:{parts[0]}-{parts[-1]}"
                )
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{DAY}:{tail}:{position}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    is_independent=False,
                )
                if position <= 2:
                    cs.add("outcome", result_set_id=result, option_id=option, kind="advanced", seats=None)
                values.append((str(result), str(unit), str(option), 0, 1, 0, 0, int(row["votes"])))
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        print(f"{len(contests)} contests, {len(values)} candidate totals, {len(added)} new elections and units")
        return cs.write()
