import base64
import csv
import io
import json
import re
import string
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.elstats import url as elstats
from ea.store import DATA, connect

SOURCE = "us.house_specials"
VERSION = "1"
FLORIDA = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=4/1/2025&OfficialResults=Y"
VIRGINIA = elstats("va2.elstats", 2025)
TENNESSEE = "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/20251202AllbyPrecinct.xlsx"
TENNESSEE_FILE = Path.home() / "Downloads" / "20251202AllbyPrecinct.xlsx"
ARIZONA = "https://apps.azsos.gov/election/2025/sge-canvass/2025-1014-Official-Arizona-State-Special-General-Election-Canvass.pdf"
TEXAS = "https://goelect.txelections.civixapps.com/api-ivis-system/api/s3/enr/election/{}"
ORDINALS = {1: "1st", 6: "6th", 7: "7th", 11: "11th", 18: "18th"}
STATES = {
    "FL": ("Florida", "12"),
    "VA": ("Virginia", "51"),
    "AZ": ("Arizona", "04"),
    "TN": ("Tennessee", "47"),
    "TX": ("Texas", "48"),
}
PARTIES = {
    "DEM": "democratic-party",
    "Democratic": "democratic-party",
    "REP": "republican-party",
    "Republican": "republican-party",
    "LPF": "libertarian-party",
    "GRN": "green-party",
    "GRE": "green-party",
}
SOURCES = {
    "FL": (
        "Florida Division of Elections",
        "Official results extract, April 1, 2025 special general election",
        FLORIDA,
    ),
    "VA": (
        "Virginia Department of Elections",
        "Election statistics archive, 2025",
        "https://historical.elections.virginia.gov",
    ),
    "AZ": ("Arizona Secretary of State", "State of Arizona Official Canvass, 2025 Special General Election", ARIZONA),
    "TN": (
        "Tennessee Secretary of State",
        "December 2, 2025 special general election results by precinct",
        TENNESSEE,
    ),
    "TX": (
        "Texas Secretary of State",
        "Official election results",
        "https://goelect.txelections.civixapps.com/ivis-enr-ui/races",
    ),
}
ARIZONA_FILE = DATA / "inbox" / "manual" / "MF-004" / ARIZONA.rsplit("/", 1)[1]
ARIZONA_NOTE = (
    "Transcribed from the Arizona Secretary of State's official canvass, certified on October 14, 2025. The canvass "
    "is a scanned PDF with no text layer; a person saved it from a browser because the site blocks scripts, and the "
    "archived copy was checked against these numbers. Nine write-in candidates' 29 votes are combined into one "
    "write-in line."
)
ARIZONA_VOTES: list[tuple[str | None, str | None, int]] = [
    ("Daniel Francis Butierez Sr.", "REP", 29944),
    ("Adelita Grijalva", "DEM", 70148),
    ("Eduardo Quintana", "GRN", 1118),
    ("Richard Grayson", None, 537),
    (None, None, 29),
]
CONTESTS = {
    "2025-04-01:FL:house:1:special": ("Patronis", None),
    "2025-04-01:FL:house:6:special": ("Fine", None),
    "2025-09-09:VA:house:11:special": ("Walkinshaw", None),
    "2025-09-23:AZ:house:7:special": ("Grijalva", None),
    "2025-11-04:TX:house:18:special": ("Menefee", 51742),
    "2026-01-31:TX:house:18:special:runoff": ("Menefee", 54612),
    "2025-12-02:TN:house:7:special": ("Van Epps", None),
}


def fetch() -> None:
    raw.fetch(SOURCE, FLORIDA, suffix=".txt")
    raw.fetch(SOURCE, VIRGINIA, suffix=".csv")
    for _, election in CONTESTS.values():
        if election is not None:
            raw.fetch(SOURCE, TEXAS.format(election), suffix=".json")
    try:
        raw.cached(SOURCE, TENNESSEE)
    except LookupError:
        raw.store(
            SOURCE,
            TENNESSEE,
            TENNESSEE_FILE.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime.fromtimestamp(TENNESSEE_FILE.stat().st_mtime, UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    try:
        raw.cached(SOURCE, ARIZONA)
    except LookupError:
        raw.store(
            SOURCE,
            ARIZONA,
            ARIZONA_FILE.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(ARIZONA_FILE.stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def emit() -> Path:
    votes: dict[str, list[tuple[str | None, str | None, int]]] = {key: [] for key in CONTESTS}
    florida: dict[tuple[str, str, str], int] = {}
    for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, FLORIDA).path.read_text()), delimiter="\t"):
        if row["RaceCode"] == "USR":
            label = " ".join(f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split())
            entry = (str(int(row["Juris1num"])), row["PartyCode"], label)
            florida[entry] = florida.get(entry, 0) + int(row["CanVotes"])
    for (district, party, label), count in florida.items():
        if party != "WRI":
            votes[f"2025-04-01:FL:house:{district}:special"].append((label, None if party == "NPA" else party, count))
    for district in ("1", "6"):
        votes[f"2025-04-01:FL:house:{district}:special"].append(
            (
                None,
                None,
                sum(count for (place, party, _), count in florida.items() if place == district and party == "WRI"),
            )
        )
    virginia: dict[str, tuple[str, int]] = {}
    for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, VIRGINIA).path.read_text())):
        if (
            row["office_name"] == "U.S. House"
            and row["election_type"] == "Special Election"
            and row["division_type"] == "Locality"
            and row["candidate_name"] != "Total Votes Cast"
        ):
            party, count = virginia.get(row["candidate_name"], (row["candidate_party_name"], 0))
            virginia[row["candidate_name"]] = (party, count + int(row["votes"]))
    votes["2025-09-09:VA:house:11:special"] = [
        (None if label == "Write-Ins" else label, party or None, count) for label, (party, count) in virginia.items()
    ]
    votes["2025-09-23:AZ:house:7:special"] = ARIZONA_VOTES
    tennessee: dict[tuple[str, str], int] = {}
    for record in pl.read_excel(raw.cached(SOURCE, TENNESSEE).path).iter_rows(named=True):
        for index in range(1, 7):
            if record[f"RNAME{index}"]:
                entry = (str(record[f"RNAME{index}"]), str(record[f"PARTY{index}"]))
                tennessee[entry] = tennessee.get(entry, 0) + int(record[f"PVTALLY{index}"] or 0)
    votes["2025-12-02:TN:house:7:special"] = [
        (label, None if party == "Independent" else party, count) for (label, party), count in tennessee.items()
    ]
    for key, (_, election) in CONTESTS.items():
        if election is not None:
            federal = json.loads(
                base64.b64decode(json.loads(raw.cached(SOURCE, TEXAS.format(election)).path.read_text())["Federal"])
            )
            votes[key] = [
                (
                    re.sub(r"\b(Ii|Iii|Iv)\b", lambda match: match[0].upper(), string.capwords(candidate["N"])),
                    None if candidate["P"] == "IND" else candidate["P"],
                    candidate["V"],
                )
                for race in federal["Races"]
                for candidate in race["Candidates"]
            ]

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2025 U.S. House special elections in Florida, Virginia, Arizona, Texas, and Tennessee, "
            "from each state's official results",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        house = cs.key("us.body", "house")
        systems = {code: cs.key("us.electoral_system", code) for code in ("fptp", "two_round")}
        if cs.minted & {house, *systems.values()}:
            raise ValueError("the House and its electoral systems must already exist")
        sources: dict[str, UUID] = {}
        for state, (publisher, title, link) in SOURCES.items():
            sources[state] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[state],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=link,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, FLORIDA), sources["FL"])
        cs.artifact(raw.cached(SOURCE, VIRGINIA), sources["VA"])
        cs.artifact(raw.cached(SOURCE, TENNESSEE), sources["TN"])
        cs.artifact(raw.cached(SOURCE, ARIZONA), sources["AZ"])
        for _, election in CONTESTS.values():
            if election is not None:
                cs.artifact(raw.cached(SOURCE, TEXAS.format(election)), sources["TX"])

        elections: dict[str, UUID] = {}
        created: set[UUID] = set()
        contests: dict[str, UUID] = {}
        for key, (winner, _) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            state, district = key.split(":")[1], int(key.split(":")[3])
            label, fips = STATES[state]
            runoff = key.endswith(":runoff")
            general = cs.key("us.contest", f"2024-11-05:{state}:house:{district}")
            row = conn.execute(
                "select district_area_id, boundary_set_id from contest where id = %s", [general]
            ).fetchone()
            if general in cs.minted or row is None:
                raise ValueError(f"{key}: the 2024 general must already exist")
            election_key = f"{key[:10]}:{state}" + (":runoff" if runoff else "" if state == "TX" else ":special")
            if election_key not in elections:
                elections[election_key] = cs.key("us.election", election_key)
                if elections[election_key] in cs.minted:
                    cs.add(
                        "election",
                        id=elections[election_key],
                        polity_id=cs.polity(f"US-{state}"),
                        parent_id=None,
                        kind="runoff" if runoff else "special",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(
                        elections[election_key],
                        f"{day:%B} {day.day}, {day.year} {label} {'special runoff' if runoff else 'special'} election",
                    )
                    cs.add(
                        "slug",
                        path=f"us/{state.lower()}/{key[:10]}-{'runoff' if runoff else 'special'}",
                        entity_id=elections[election_key],
                    )
            contest = contests[key] = cs.key("us.contest", key)
            previous = contests.get(key.removesuffix(":runoff")) if runoff else None
            cs.add(
                "contest",
                id=contest,
                election_id=elections[election_key],
                kind="body_seats",
                body_id=house,
                district_area_id=row[0],
                seat_id=None,
                term="remainder",
                boundary_set_id=row[1],
                party_scope_id=None,
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=systems["two_round" if state == "TX" else "fptp"],
                default_result_kind="counted",
            )
            name(
                contest,
                f"2025 U.S. House special election"
                f"{' runoff' if runoff else ''} in {label}'s {ORDINALS[district]} congressional district",
            )
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{key[:10]}-{'runoff' if runoff else 'special' if state != 'TX' else 'general'}"
                f"/house-{district}-special",
                entity_id=contest,
            )
            known = {
                slug(text): person
                for text, person in conn.execute(
                    """select distinct n.text, c.person_id from candidacy c
                       join contest_option o on o.id = c.option_id join contest ct on ct.id = o.contest_id
                       join name n on n.entity_id = c.person_id and n.is_preferred
                       where ct.district_area_id = %s""",
                    [row[0]],
                ).fetchall()
            }
            result_set = cs.key(f"{SOURCE}.result_set", key)
            unit = cs.unit("us.cd", f"{fips}{district:02d}", day)
            total = sum(count for _, _, count in votes[key])
            ranked = sorted((count for label, _, count in votes[key] if label is not None), reverse=True)
            options: list[tuple[UUID, int]] = []
            for text, party_code, count in votes[key]:
                if text is None:
                    option = cs.key("us.option", f"{key}:write_in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    options.append((option, count))
                    continue
                if winner in text:
                    person = conn.execute(
                        """select t.person_id from tenure t join name n on n.entity_id = t.person_id and n.is_preferred
                           where t.body_id = %s and t.from_date > %s and n.text ilike %s order by t.from_date limit 1""",
                        [house, day, f"%{winner}%"],
                    ).fetchone()
                    if person is None:
                        raise ValueError(f"{key}: no House tenure for {winner}")
                    person = person[0]
                else:
                    person = known.get(slug(text)) or cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                    if person in cs.minted and person not in created:
                        created.add(person)
                        cs.add("person", id=person)
                        name(person, text)
                party = None if party_code is None else cs.key("us.party", PARTIES[party_code])
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option", id=option, contest_id=contest, kind="candidate", party_id=party, ballot_label=text
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=text,
                    is_independent=party is None,
                    is_incumbent=False,
                )
                advanced = state == "TX" and not runoff and count >= ranked[1]
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="advanced" if advanced else "elected" if winner in text else "lost",
                )
                options.append((option, count))
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[election_key],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            if state == "AZ":
                cs.add("note", entity_id=result_set, kind="methodology", body_md=ARIZONA_NOTE)
            for option, count in options:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=1,
                    flags=0,
                    value=count,
                )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=1,
                flags=0,
                value=total,
            )
        return cs.write()
