import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.la_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Official election results"
DATA_URL = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob="
DATES = DATA_URL + "ElectionDates.htm"
OFFICES = {
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary of State": "Secretary of State",
    "Attorney General": "Attorney General",
    "Treasurer": "Treasurer",
    "State Treasurer": "Treasurer",
    "Commissioner of Agriculture and Forestry": "Commissioner of Agriculture and Forestry",
    "Commissioner of Agriculture": "Commissioner of Agriculture and Forestry",
    "Commissioner of Insurance": "Commissioner of Insurance",
    "Superintendent of Education": "Superintendent of Education",
    "Commissioner of Elections": "Commissioner of Elections",
    "Comptroller": "Comptroller",
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LBT": "libertarian-party",
    "GRN": "green-party",
    "CON": "constitution-party",
    "REF": "reform-party",
}
INDEPENDENT = {"IND", "NOPTY", "NPTY", "NOP"}
PARISHES = [f"{number:02}" for number in range(1, 65)]
RUNOFF = (
    "Louisiana elects its statewide officers in an open primary in which every candidate runs regardless of party; "
    "if no one wins a majority, the top two meet in a runoff."
)

REPORT = "https://archive.org/download/micro_IA40706905_0363/micro_IA40706905_0363.pdf"
LAMBERT_TREEN_1979 = [
    (9661, 9822),
    (6496, 3267),
    (17012, 3008),
    (5675, 3497),
    (9768, 7020),
    (4473, 4848),
    (3886, 2999),
    (8018, 11731),
    (28959, 41337),
    (29361, 26678),
    (2523, 2176),
    (2061, 1979),
    (3294, 2388),
    (3232, 3093),
    (4903, 4327),
    (5406, 3758),
    (54302, 57666),
    (2118, 1675),
    (5162, 2014),
    (8913, 7021),
    (5178, 4660),
    (4183, 2964),
    (6996, 13616),
    (11247, 3573),
    (4944, 3050),
    (46047, 83099),
    (5420, 6473),
    (13448, 30008),
    (11714, 17433),
    (2982, 3286),
    (4428, 6760),
    (16344, 5674),
    (3005, 2022),
    (6092, 5847),
    (6618, 5969),
    (79057, 74676),
    (18032, 23840),
    (2150, 5443),
    (6916, 2582),
    (19517, 19472),
    (3306, 2044),
    (3786, 3856),
    (4702, 3655),
    (14708, 15215),
    (8008, 6437),
    (3929, 1404),
    (7482, 2038),
    (9693, 4331),
    (17273, 13424),
    (7353, 7665),
    (8390, 11651),
    (14363, 21527),
    (19357, 12003),
    (1982, 1457),
    (9601, 18107),
    (4509, 4625),
    (9908, 11934),
    (7192, 5268),
    (12597, 7254),
    (8405, 6951),
    (5047, 1950),
    (2828, 2604),
    (2808, 1115),
    (4366, 3425),
]


def blob(day: date, name: str) -> str:
    return f"{DATA_URL}{day:%Y%m%d}/{name}"


def many(value: Any) -> list[Any]:
    return [value] if isinstance(value, dict) else value or []


def days() -> list[date]:
    listed = json.loads(raw.cached(SOURCE, DATES).path.read_text())["Dates"]["Date"]
    return sorted(datetime.strptime(entry["ElectionDate"], "%m/%d/%Y").date() for entry in listed)


def statewide(day: date) -> list[dict[str, Any]]:
    races = json.loads(raw.cached(SOURCE, blob(day, "RacesCandidates_Multiparish.htm")).path.read_text())
    return [
        race
        for race in many((races.get("Races") or {}).get("Race"))
        if race["GeneralTitle"] in OFFICES and race.get("IsMultiParish") == "1"
    ]


def fetch() -> None:
    raw.fetch(SOURCE, DATES, suffix=".json")
    raw.fetch(SOURCE, REPORT, suffix=".pdf")
    lock = threading.Lock()

    def get(link: str) -> None:
        with lock:
            try:
                raw.cached(SOURCE, link)
                return
            except LookupError:
                pass
        for attempt in range(8):
            response = httpx.get(link, headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=600)
            if response.status_code not in (403, 429):
                break
            time.sleep(2**attempt)
        response.raise_for_status()
        with lock:
            raw.store(
                SOURCE,
                link,
                response.content,
                suffix=".json",
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", "application/json"),
            )

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(get, [blob(day, "RacesCandidates_Multiparish.htm") for day in days()]))
    chosen = [day for day in days() if statewide(day)]
    links = [blob(day, "Votes_Multiparish.htm") for day in chosen] + [
        blob(day, f"VotesParish/Votes_{parish}.htm") for day in chosen for parish in PARISHES
    ]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(get, links))


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day in days():
        races = statewide(day)
        if not races:
            continue
        totals = {
            race["ID"]: {choice["ID"]: (int(choice["VoteTotal"]), choice["Outcome"]) for choice in many(race["Choice"])}
            for race in many(
                json.loads(raw.cached(SOURCE, blob(day, "Votes_Multiparish.htm")).path.read_text())["Races"]["Race"]
            )
        }
        counted: dict[tuple[str, str], int] = {}
        for parish in PARISHES:
            listed = json.loads(raw.cached(SOURCE, blob(day, f"VotesParish/Votes_{parish}.htm")).path.read_text())
            by_race = {race["ID"]: race for race in many((listed.get("Races") or {}).get("Race"))}
            for race in races:
                if race["ID"] not in by_race:
                    continue
                for choice in many(by_race[race["ID"]]["Choice"]):
                    votes = int(choice["VoteTotal"])
                    counted[(race["ID"], choice["ID"])] = counted.get((race["ID"], choice["ID"]), 0) + votes
                    rows.append(
                        {"day": day, "race": race["ID"], "choice": choice["ID"], "parish": parish, "votes": votes}
                    )
        for race in races:
            seats = int(race["NumberToBeElected"] or 1)
            for choice in many(race["Choice"]):
                total, outcome = totals[race["ID"]][choice["ID"]]
                if counted.get((race["ID"], choice["ID"]), 0) != total:
                    raise ValueError(f"{day} {race['SpecificTitle']}: {choice['Desc']}'s parishes don't sum to {total}")
                found = re.match(r"^(.*?)\s*\(([A-Z]+)\)$", choice["Desc"].strip())
                rows.append(
                    {
                        "day": day,
                        "race": race["ID"],
                        "choice": choice["ID"],
                        "parish": None,
                        "votes": total,
                        "office": OFFICES[race["GeneralTitle"]],
                        "title": race["SpecificTitle"],
                        "seats": seats,
                        "candidate": (found[1] if found else choice["Desc"]).strip(),
                        "party": found[2] if found else None,
                        "outcome": outcome,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    choices = frame.filter(pl.col("parish").is_null()).drop("parish", "votes")
    (
        frame.filter(pl.col("parish").is_not_null())
        .select("day", "race", "choice", "parish", "votes")
        .join(choices, on=["day", "race", "choice"])
        .sort("day", "race", "choice", "parish")
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's statewide executive elections by parish, 1982–2026, with their open primaries and runoffs",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://voterportal.sos.la.gov/static/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, DATES), source)
        for day in sorted(set(frame["day"])):
            for name_ in ["RacesCandidates_Multiparish.htm", "Votes_Multiparish.htm"] + [
                f"VotesParish/Votes_{parish}.htm" for parish in PARISHES
            ]:
                cs.artifact(raw.cached(SOURCE, blob(day, name_)), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-LA'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        parishes: dict[str, UUID] = {
            f"{index + 1:02}": unit
            for index, (unit,) in enumerate(
                conn.execute(
                    """select u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like '22%' order by n.text"""
                ).fetchall()
            )
        }
        if len(parishes) != 64:
            raise ValueError("Louisiana needs 64 parishes")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        others: dict[str, list[tuple[UUID, set[str]]]] = {}
        for key, option, label in conn.execute(
            """select e.value, o.id, o.ballot_label from external_id e
               join contest c on c.id = e.entity_id join changeset s on s.id = c.changeset_id
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               where e.scheme = 'us.contest' and e.value like '%%:LA:%%' and s.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            others.setdefault(key, []).append((option, set(slug(label or "").split("-"))))
        two_round = cs.key("us.electoral_system", "two_round")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {two_round, *parties.values()}:
            raise ValueError("the two-round system and the national parties must exist")
        persons: dict[str, UUID] = {}
        runoffs: dict[str, tuple[date, UUID]] = {}
        races = frame.group_by("day", "race", maintain_order=True).agg(
            pl.col("office", "title", "seats").first(), pl.col("outcome")
        )
        for day, race, office, title, seats, outcomes in races.sort("day").iter_rows():
            pending = runoffs.get(office)
            round_number = 2 if pending is not None and 0 < (day - pending[0]).days <= 70 else 1
            kind = "runoff" if round_number == 2 else "general"
            election_key = f"{day}:LA" + (":runoff" if round_number == 2 else "")
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-LA"),
                    parent_id=cs.key("us.election", str(day)) if kind == "general" and str(day) in known else None,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"{day.year} Louisiana {'runoff' if kind == 'runoff' else 'election'} ({day:%B} {day.day})",
                )
                cs.add("slug", path=f"us/la/{day}-{kind}", entity_id=election)
            office_slug = slug(office)
            office_id = cs.key("us.office", f"{office_slug}:LA")
            if f"{office_slug}:LA" not in offices_known:
                offices_known.add(f"{office_slug}:LA")
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity("US-LA"),
                    kind="governor" if office == "Governor" else "other",
                )
                name(office_id, f"{office} of Louisiana")
            remainder = bool(re.search(r"(?i)unexpired|special", title))
            contest_key = f"{day}:LA:{office_slug}" + (":special" if remainder else "")
            contest = cs.key("us.contest", contest_key)
            if "Runoff" in outcomes:
                runoffs[office] = (day, contest)
            reused = contest_key in others
            if not reused:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=area,
                    term="remainder" if remainder else "full",
                    magnitude=seats,
                    round_number=round_number,
                    previous_round_id=pending[1] if round_number == 2 and pending is not None else None,
                    electoral_system_id=two_round,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{day.year} Louisiana {office} "
                    + ("runoff" if round_number == 2 else "special election" if remainder else "election"),
                )
                cs.add(
                    "slug",
                    path=f"us/la/{day}-{kind}/{office_slug}" + ("-special" if remainder else ""),
                    entity_id=contest,
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=not reused,
                completeness=1,
            )
            if round_number == 1 and not reused:
                cs.add("note", entity_id=result_set, kind="context", body_md=RUNOFF)
            lines = frame.filter((pl.col("day") == day) & (pl.col("race") == race))
            for (choice, candidate, label, outcome), cells in lines.group_by(
                "choice", "candidate", "party", "outcome", maintain_order=True
            ):
                surname = [word for word in slug(candidate).split("-") if word not in ("jr", "sr", "ii", "iii")][-1]
                option = next((option for option, words in others.get(contest_key, []) if surname in words), None)
                if option is None:
                    if candidate not in persons:
                        persons[candidate] = cs.key(f"{SOURCE}.person", f"LA:{slug(candidate)}")
                        if persons[candidate] in cs.minted:
                            cs.add("person", id=persons[candidate])
                            name(persons[candidate], candidate)
                    party = parties.get(label or "")
                    option = cs.key("us.option", f"{contest_key}:{persons[candidate]}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{persons[candidate]}:head"),
                        person_id=persons[candidate],
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=label in INDEPENDENT,
                    )
                for parish, votes in cells.select("parish", "votes").iter_rows() if result_set in cs.minted else ():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=parishes[parish],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                if not reused and outcome in ("Elected", "Runoff"):
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if outcome == "Elected" else "advanced",
                        seats=1 if outcome == "Elected" else None,
                    )
        report = cs.source(PUBLISHER, "Report of the Secretary of State, 1979–1980")
        if report in cs.minted:
            cs.add(
                "source",
                id=report,
                kind="official_body",
                publisher=PUBLISHER,
                title="Report of the Secretary of State, 1979–1980",
                url="https://archive.org/details/micro_IA40706905_0363",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, REPORT), report)
        ((primary,),) = conn.execute(
            "select entity_id from external_id where scheme = 'us.contest' and value = '1979-10-27:LA:governor'"
        ).fetchall()
        persons79 = dict(
            conn.execute(
                """select o.ballot_label, d.person_id from contest_option o join candidacy d on d.option_id = o.id
                   where o.contest_id = %s""",
                [primary],
            ).fetchall()
        )
        counties = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.county' and code like '22%%'")
        )
        contest = cs.key("us.contest", "1979-12-08:LA:governor")
        cs.add(
            "contest",
            id=contest,
            election_id=cs.key("us.election", "1979-12-08:LA:runoff"),
            kind="office",
            office_id=cs.key("us.office", "governor:LA"),
            district_area_id=area,
            term="full",
            magnitude=1,
            round_number=2,
            previous_round_id=primary,
            electoral_system_id=two_round,
            default_result_kind="counted",
        )
        name(contest, "1979 Louisiana Governor runoff")
        cs.add("slug", path="us/la/1979-12-08-runoff/governor", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", "1979-12-08:LA:governor")
        cs.add(
            "result_set",
            id=result_set,
            election_id=cs.key("us.election", "1979-12-08:LA:runoff"),
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=report,
            is_default=True,
            completeness=1,
        )
        for column, (label, ballot, party) in enumerate(
            [("Louis Lambert", "Louis Lambert", "DEM"), ("David Conner Treen", "David C. Treen", "REP")]
        ):
            option = cs.key("us.option", f"1979-12-08:LA:governor:{persons79[label]}")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=parties[party],
                ballot_label=ballot,
            )
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"1979-12-08:LA:governor:{persons79[label]}:head"),
                person_id=persons79[label],
                option_id=option,
                role="head",
                ballot_name=ballot,
                is_independent=False,
            )
            for index, votes in enumerate(LAMBERT_TREEN_1979) if result_set in cs.minted else ():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[f"22{index * 2 + 1:03}"],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes[column],
                )
            if label == "David Conner Treen":
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        if not conn.execute("select 1 from citation where entity_id = %s", [result_set]).fetchall():
            cs.add("citation", entity_id=result_set, source_id=report, locator={"page": 42, "insert": "E"})
        return cs.write()
