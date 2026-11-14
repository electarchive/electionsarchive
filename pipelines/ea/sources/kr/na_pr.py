import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.kr import na
from ea.sources.kr.nec import PARTIES_QUERY, REPORT, SIDO, _bindings, _get
from ea.store import DATA, connect

SOURCE = "kr.na_pr"
LIST_DAYS = tuple(day for day in na.DATES if day >= "20040415")
SEAT_DAYS = tuple(day for day in na.DATES if day >= "19631126" and day not in {"19730227", "19781212"})
PARSED = DATA / "parsed" / SOURCE


def _form(kind: str, day: str, city: str) -> dict[str, str]:
    form = na._form("winners" if kind == "winners" else "count", day, city)
    form["electionCode"] = "7"
    return form


def key(kind: str, day: str, city: str) -> str:
    return f"{REPORT}?{urlencode(_form(kind, day, city))}"


def _job(job: tuple[str, str, str]) -> None:
    address = key(*job)
    if address in raw.keys(SOURCE) and 'id="table01"' in raw.cached(SOURCE, address).path.read_text():
        return
    content = _get(REPORT, _form(*job))
    if 'id="table01"' not in content.decode():
        raise ValueError(f"{job}: no table")
    raw.store(SOURCE, address, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


def jobs() -> list[tuple[str, str, str]]:
    found = [("winners", day, "-1") for day in SEAT_DAYS]
    for day in LIST_DAYS:
        found.extend(("count", day, city) for city, _ in na.cities(day))
    return found


def fetch() -> None:
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(_job, jobs()))


def _parties(html: str) -> list[str]:
    head = html[html.find('id="table01"') : html.find("<tbody")]
    return [na._text(name) for name in re.findall(r"<strong>(.*?)</strong>", head, re.DOTALL)]


def _counts(day: str, sido: str, html: str) -> list[dict[str, Any]]:
    names = _parties(html)
    found = []
    for row in na.table(html):
        if len(row) != len(names) + 6 or not row[0] or not na._numeric(row[1]):
            continue
        found.append(
            {
                "day": day,
                "sido": sido,
                "town": row[0],
                "registered": na._number(row[1]),
                "ballots": na._number(row[2]),
                "votes": [na._number(cell) if cell else 0 for cell in row[3 : 3 + len(names)]],
                "valid": na._number(row[3 + len(names)]),
                "invalid": na._number(row[4 + len(names)]),
                "parties": names,
            }
        )
    return found


def _members(day: str, html: str) -> list[dict[str, Any]]:
    return [
        {
            "day": day,
            "party": row[0],
            "rank": int(row[1]),
            "name": re.sub(r"\s*\(.*\)$", "", row[2]),
            "birth": row[4].split(" ")[0],
        }
        for row in na.table(html)
        if len(row) == 8 and row[1].isdigit()
    ]


def parse() -> None:
    results: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for day in SEAT_DAYS:
        members.extend(_members(day, raw.cached(SOURCE, key("winners", day, "-1")).path.read_text()))
    for day in LIST_DAYS:
        for city, _ in na.cities(day):
            results.extend(_counts(day, na._sido(city), raw.cached(SOURCE, key("count", day, city)).path.read_text()))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(results).write_parquet(PARSED / "results.parquet")
    pl.DataFrame(members).write_parquet(PARSED / "members.parquet")
    frame = pl.DataFrame(results)
    print(frame.group_by("day").agg(pl.len(), pl.col("valid").sum(), pl.col("ballots").sum()).sort("day"))
    print(pl.DataFrame(members).group_by("day").agg(pl.len()).sort("day"))


VERSION = "1"
PUBLISHER = na.PUBLISHER
COUNT_TITLE = "선거통계시스템: 역대선거 비례대표국회의원선거 개표현황"
MEMBER_TITLE = "선거통계시스템: 역대선거 비례대표국회의원선거 당선인 명부"
COUNT_LANDING = na.LANDING
MEMBER_LANDING = na.WINNERS_LANDING
LIST_SYSTEM = (
    "Voters cast a second ballot for a party. Parties with at least 3 percent of the valid list votes or five "
    "constituency seats share the proportional seats by the largest remainder of the Hare quota, and each party "
    "fills its seats in list order; a vacancy passes to the next candidate on the list (Public Official Election "
    "Act, arts. 189 and 200)."
)
LINKED = (
    "In 2020 the proportional seats were shared by a semi-linked formula that offset constituency seats for 30 of "
    "the 47 seats and by the Hare quota for the other 17. The archive stores no electoral system for this contest "
    "because its rules cannot yet be expressed."
)
EARLY = (
    "Before 2004 there was no separate list ballot: the proportional (전국구) seats were allotted to parties from "
    "their constituency results, so the National Election Commission prints no list votes. This contest holds the "
    "party list of the elected members and each party's seats as declared."
)


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    party_items: dict[str, list[tuple[str, str | None, str | None, str | None]]] = {}
    for row in _bindings(PARTIES_QUERY):
        party_items.setdefault(row["label"], []).append(
            (row["party"].rsplit("/", 1)[1], row.get("english"), row.get("start"), row.get("end"))
        )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "South Korea's National Assembly proportional seats: elected members of the national lists "
                "1963–2024 and party-list votes by city, county, and district 2004–2024, from the National "
                "Election Commission's statistics system"
            ),
            polity="KR",
        )

        def name(entity: UUID, text: str, lang: str = "ko", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        korea = cs.polity("KR")
        sources = {}
        for code, title, landing in (("count", COUNT_TITLE, COUNT_LANDING), ("member", MEMBER_TITLE, MEMBER_LANDING)):
            sources[code] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[code],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=landing,
                license=na.LICENSE,
                license_url=na.BASE,
            )
        for address in raw.keys(SOURCE):
            cs.artifact(raw.cached(SOURCE, address), sources["member" if "EPEI01" in address else "count"])
        sidos = {code: cs.area("kr.sido", code) for code in SIDO}
        units: dict[str, UUID] = {}

        def unit(scheme: str, code: str, parent: UUID | None, area: UUID) -> UUID:
            if code not in units:
                entity = units[code] = cs.unit(scheme, code)
                if entity in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=entity,
                        polity_id=korea,
                        scheme_code=scheme,
                        code=code,
                        parent_id=parent,
                        area_relation="equals" if scheme == "kr.sido" else "within",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(entity, code.rsplit("/", 1)[-1])
            return units[code]

        body = cs.key("kr.na.body", "assembly")
        system = cs.key(f"{SOURCE}.electoral_system", "list")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=korea,
            rules={
                "formula": "hare",
                "threshold": {"level": "party", "scope": "national", "percent": 3, "constituencies": 5},
                "lists": {"order": "closed", "vacancy": "next_on_list"},
            },
            rules_version=1,
        )
        name(system, "National Assembly proportional seats, party list ballot (2004–2016, 2024)", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=LIST_SYSTEM)

        parties: dict[tuple[str, str], UUID] = {}
        minted_parties: set[UUID] = set()

        def party(text: str, day: str) -> UUID:
            if (text, day) not in parties:
                held = f"{day[:4]}-{day[4:6]}-{day[6:]}"
                found = [
                    item
                    for item, _, start, end in party_items.get(text, [])
                    if (start is None or start[:10] <= held) and (end is None or end[:10] >= held)
                ]
                key = ("wikidata", found[0]) if len(set(found)) == 1 else ("kr.na.party", f"{text}:{day}")
                entity = parties[text, day] = cs.key(*key)
                if entity in cs.minted and entity not in minted_parties:
                    minted_parties.add(entity)
                    cs.add("party", id=entity, kind="party", polity_id=korea)
                    name(entity, text)
                    english = next((e for item, e, _, _ in party_items.get(text, []) if key[1] == item and e), None)
                    if english:
                        name(entity, english, "en", "english")
            return parties[text, day]

        for day in SEAT_DAYS:
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            number = na.DATES.index(day) + 1
            election = cs.key("kr.na.election", day)
            if election in cs.minted:
                raise ValueError(f"{day}: the constituency election is not loaded")
            path = f"kr/{held}-legislative"
            elected = members.filter(pl.col("day") == day)
            counted = results.filter(pl.col("day") == day)
            listed = counted.height > 0
            contest = cs.key(f"{SOURCE}.contest", day)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                magnitude=elected.height,
                electoral_system_id=system if listed and day != "20200415" else None,
                default_result_kind="counted",
            )
            name(contest, f"제{number}대 국회의원선거 비례대표")
            name(contest, f"{held.year} South Korean legislative election, proportional seats", "en", "english")
            cs.add("slug", path=f"{path}/proportional", entity_id=contest)
            if not listed:
                cs.add("note", entity_id=contest, kind="methodology", body_md=EARLY)
            elif day == "20200415":
                cs.add("note", entity_id=contest, kind="methodology", body_md=LINKED)
            result_set = cs.key(f"{SOURCE}.result_set", day)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources["count" if listed else "member"],
                is_default=True,
                completeness=1,
            )
            seats = dict(elected.group_by("party").len().iter_rows())
            names = counted["parties"][0].to_list() if listed else list(seats)
            if set(seats) - set(names):
                raise ValueError(f"{day}: {set(seats) - set(names)} won seats but have no ballot column")
            options: dict[str, UUID] = {}
            for position, text in enumerate(names, 1):
                options[text] = cs.key(f"{SOURCE}.option", f"{day}:{text}")
                member = party(text, day)
                cs.add(
                    "contest_option",
                    id=options[text],
                    contest_id=contest,
                    kind="list",
                    party_id=member,
                    ballot_position=position,
                    ballot_label=text,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[text],
                    kind="elected" if text in seats else "lost",
                    seats=seats.get(text),
                )
            for text, rank, person_name in elected.select("party", "rank", "name").rows():
                person = cs.key(f"{SOURCE}.person", f"{day}:{text}:{rank}")
                cs.add("person", id=person)
                name(person, person_name)
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{day}:{text}:{rank}"),
                    person_id=person,
                    option_id=options[text],
                    role="list_member",
                    list_position=rank,
                    ballot_name=person_name,
                    member_of_party_id=party(text, day),
                    is_independent=False,
                )
            for record in counted.to_dicts():
                sido = record["sido"]
                top = unit("kr.sido", sido, None, sidos[sido])
                if record["town"] == "합계":
                    continue
                town = re.sub(r"\((?:[^()]*)\)$", "", record["town"]).strip()
                place = unit("kr.sigungu", f"{sido}/{town}", top, sidos[sido])
                for measure, option, value in (
                    (2, None, record["registered"]),
                    (3, None, record["ballots"]),
                    (4, None, record["valid"]),
                    (5, None, record["invalid"]),
                    *((1, options[text], v) for text, v in zip(names, record["votes"], strict=True)),
                ):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=place,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
