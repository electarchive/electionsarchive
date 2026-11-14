import json
import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.vi_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOST = "https://electionarchive.vivote.gov"
PUBLISHER = "Election System of the Virgin Islands"
TITLE = "Virgin Islands Elections Database, Legislature, Board of Education, and Board of Elections"
OFFICES = {"5": "legislature", "562": "board-of-education", "563": "board-of-elections"}
BODIES = {
    "legislature": "Legislature",
    "board-of-education": "Board of Education",
    "board-of-elections": "Board of Elections",
}
DISTRICTS = {
    "St. Croix": "st-croix-district",
    "St. Thomas": "st-thomas-district",
    "St. John": "st-john-district",
    "U.S. Virgin Islands": "at-large",
}
LABELS = {
    "st-croix-district": "St. Croix District",
    "st-thomas-district": "St. Thomas District",
    "st-john-district": "St. John District",
    "st-thomas-st-john-district": "St. Thomas-St. John District",
    "at-large": "At-Large",
}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Independent Citizens Movement": "VI:independent-citizens-movement",
}
REPORTS = {
    date(2020, 11, 3): (
        "us.vi_precincts",
        "https://www.vivote.gov/sites/default/files/officialresults/Official%20Precinct%20Report.pdf",
        "2020 General Election Precinct Report, Official Results",
        "Contest totals summed over the report's 29 precinct sections, absentee and provisional included; the archive's 2020 general rows carry ballots cast but no candidate votes.",
    ),
    date(2024, 11, 5): (
        SOURCE,
        "https://vivote.gov/wp-content/uploads/2024/11/Official-Territorial-Election-Summary-11.15.2024.pdf",
        "2024 General Election Official Territorial Election Summary",
        "Totals include absentee and provisional ballots, which the archive's 2024 general rows leave out.",
    ),
}
SECTIONS = {
    "legislature of the virgin islands at large": ("legislature", "U.S. Virgin Islands"),
    "legislature of the virgin islands st croix": ("legislature", "St. Croix"),
    "legislature of the virgin islands st thomas st john": ("legislature", "St. Thomas/St. John"),
    "board of education at large": ("board-of-education", "U.S. Virgin Islands"),
    "board of education stx": ("board-of-education", "St. Croix"),
    "board of education st croix": ("board-of-education", "St. Croix"),
    "board of education stt stj": ("board-of-education", "St. Thomas/St. John"),
    "board of education st thomas st john": ("board-of-education", "St. Thomas/St. John"),
    "board of elections stx": ("board-of-elections", "St. Croix"),
    "board of elections st croix": ("board-of-elections", "St. Croix"),
    "board of elections stj st thomas st john": ("board-of-elections", "St. John"),
    "board of elections stt st thomas st john": ("board-of-elections", "St. Thomas"),
}
CODES = {"DEM": "Democratic", "REP": "Republican", "ICM": "Independent Citizens Movement"}
ROW = re.compile(r"^(?:([A-Z]{3}) )?(\S.*?\S)\s{2,}([\d,]+)(?:\s|$)")
PSEUDO = {"Total Ballots Cast", "Total Votes Cast", "Registered Voters", "Blank Ballots", "Overvotes/Undervotes"}


def url() -> str:
    search = {
        "global": {"years": {"from": 2000, "to": 2024}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"id": int(office)} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return f"{HOST}/api/download_search.csv?" + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def fetch() -> None:
    raw.fetch(SOURCE, url(), suffix=".csv")
    raw.fetch(SOURCE, REPORTS[date(2024, 11, 5)][1], suffix=".pdf")


def district(body: str, name: str) -> str:
    if name == "St. Thomas/St. John":
        return "st-thomas-st-john-district" if body == "board-of-elections" else "st-thomas-district"
    return DISTRICTS[name]


def report(day: date) -> pl.DataFrame:
    cache, link = REPORTS[day][:2]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(cache, link).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    section = None
    previous = ""
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        title = " ".join(re.findall(r"[a-z]+", previous.lower()))
        if line.startswith("Vote For"):
            section = (title, int(line.split()[-1])) if title in SECTIONS else None
        elif line.isupper() and line != "TOTAL":
            section = None
        elif section is not None and (row := ROW.match(line)) is not None:
            body, place = SECTIONS[section[0]]
            rows.append(
                {
                    "contest_id": f"{day}:{section[0]}",
                    "election_date": day,
                    "election_type": "General",
                    "primary_party": None,
                    "body": body,
                    "district_name": place,
                    "number_seats": section[1],
                    "candidate_name": "Write-In Votes" if row[2] == "Write-In Totals" else row[2],
                    "candidate_party_name": CODES.get(row[1] or ""),
                    "votes": int(row[3].replace(",", "")),
                    "is_winner": False,
                    "report": link,
                }
            )
        previous = line
    return pl.DataFrame(rows, schema_overrides={"primary_party": pl.String, "candidate_party_name": pl.String})


def parse() -> None:
    frame = pl.read_csv(raw.cached(SOURCE, url()).path, infer_schema=False).filter(
        pl.col("office_id").is_in(list(OFFICES)) & (pl.col("division_type") == "District")
    )
    reported = pl.col("election_date").is_in(list(REPORTS)) & (pl.col("election_type") == "General")
    PARSED.mkdir(parents=True, exist_ok=True)
    counted = ~pl.col("candidate_name").is_in(list(PSEUDO))
    archive = frame.select(
        "contest_id",
        pl.col("election_date").str.slice(0, 10).str.to_date(),
        "election_type",
        "primary_party",
        pl.col("office_id").replace_strict(OFFICES).alias("body"),
        "district_name",
        pl.col("number_seats").cast(pl.Int64),
        "candidate_name",
        "candidate_party_name",
        pl.col("votes").cast(pl.Int64),
        (pl.col("is_winner") == "true").alias("is_winner"),
        pl.lit(None, pl.String).alias("report"),
    )
    token = pl.col("candidate_name").map_elements(slug, return_dtype=pl.String).alias("token")
    keys = ["election_date", "body", "district_name", "token"]
    flags = archive.filter(reported & pl.col("is_winner")).select(*keys[:3], token).unique()
    reports = (
        pl.concat([report(day) for day in REPORTS])
        .with_columns(token)
        .join(flags.with_columns(pl.lit(True).alias("flagged")), on=keys, how="left")
        .with_columns(pl.col("flagged").fill_null(False).alias("is_winner"))
        .drop("token", "flagged")
    )
    (
        pl.concat([archive.filter(~reported), reports])
        .group_by(
            "contest_id",
            "election_date",
            "election_type",
            "primary_party",
            "body",
            "district_name",
            "number_seats",
            "candidate_name",
            "candidate_party_name",
            "report",
        )
        .agg(pl.col("votes").sum(), pl.col("is_winner").any())
        .filter((pl.col("votes").filter(counted).sum() > 0).over("contest_id"))
        .sort("election_date", "body", "district_name", "votes", descending=[False, False, False, True])
        .write_parquet(PARSED / "candidates.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virgin Islands Legislature, Board of Education, and Board of Elections generals, Democratic primaries, and the 2017 special 2010–2024 from the Elections System's archive and its official 2020 and 2024 general reports",
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
            url=HOST,
            notes="District totals summed over the archive's vote channels; at-large contests sum the St. Croix and St. Thomas-St. John districts.",
        )
        cs.artifact(raw.cached(SOURCE, url()), source)
        reports: dict[str, UUID] = {}
        for cache, link, title, notes in REPORTS.values():
            reports[link] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=reports[link],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=link,
                notes=notes,
            )
            cs.artifact(raw.cached(cache, link), reports[link])
        block = cs.key("us.electoral_system", "block")
        territory_unit, territory = conn.execute(
            """select u.id, u.area_id from reporting_unit u join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id where p.iso_code = 'US-VI' and u.scheme_code = 'us.state'"""
        ).fetchall()[0]
        polity = cs.polity("US-VI")
        democrats = cs.key("us.party", "democratic-party")
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        made: set[UUID] = set()
        units: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        for body, label in BODIES.items():
            bodies[body] = cs.key("us.body", f"VI:78:{body}")
            if bodies[body] in cs.minted:
                cs.add("body", id=bodies[body], polity_id=polity, kind="other")
                name(bodies[body], f"U.S. Virgin Islands {label}")
        for _, rows in frame.group_by(["contest_id"], maintain_order=True):
            first = rows.row(0, named=True)
            day, body, seats = first["election_date"], first["body"], first["number_seats"]
            stage = "primary" if first["election_type"] == "Primary" else "special" if day.month != 11 else "general"
            place = district(body, first["district_name"])
            election_key = {"primary": f"{day}:VI:primary", "special": f"{day}:VI:special", "general": f"{day}:VI"}[
                stage
            ]
            election = cs.key("us.election", election_key)
            if election_key not in known:
                known.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} U.S. Virgin Islands {stage} election")
                cs.add("slug", path=f"us/vi/{day}-{stage}", entity_id=election)
            if place == "at-large":
                area, unit = territory, territory_unit
            else:
                code = f"VI:78:{body}:{place}"
                area = cs.area("us.local_district", code)
                if area in cs.minted and area not in made:
                    made.add(area)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=territory, valid_from=None)
                    name(area, f"U.S. Virgin Islands {BODIES[body]}, {LABELS[place]}")
                if code not in units:
                    units[code] = cs.unit("us.local_district", code)
                    if units[code] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=units[code],
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=code,
                            parent_id=territory_unit,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
                unit = units[code]
            primary = stage == "primary"
            contest_key = f"{day}:VI:78:{body}:{place}" + (":democratic" if primary else "")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=bodies[body],
                district_area_id=area,
                term="full",
                magnitude=seats,
                party_scope_id=democrats if primary else None,
                electoral_system_id=block,
                default_result_kind="counted",
            )
            title = f"U.S. Virgin Islands {BODIES[body]}, {LABELS[place]}"
            name(
                contest,
                f"{day.year} {title} Democratic primary" if primary else f"{day.year} {title} {stage} election",
            )
            cs.add(
                "slug",
                path=f"us/vi/{day}-{stage}/{body}-{place}" + ("-democratic" if primary else ""),
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
                source_id=reports[first["report"]] if first["report"] else source,
                is_default=True,
                completeness=1,
            )
            ranked = rows.filter(~pl.col("candidate_name").is_in([*PSEUDO, "Write-In Votes"])).sort(
                "votes", descending=True
            )
            flagged = set(ranked.filter(pl.col("is_winner"))["candidate_name"])
            declared = flagged == set(ranked.head(seats)["candidate_name"]) and (
                ranked.height <= seats or ranked["votes"][seats - 1] > ranked["votes"][seats]
            )
            for candidate, party, votes, winner in rows.select(
                "candidate_name", "candidate_party_name", "votes", "is_winner"
            ).iter_rows():
                if candidate in PSEUDO:
                    continue
                if candidate == "Write-In Votes":
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    label = " ".join(candidate.split())
                    token = slug(label)
                    party_key = PARTIES.get(party or "")
                    party_id = cs.key("us.party", party_key) if party_key else None
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party_id,
                        ballot_label=label,
                    )
                    person = cs.key("us.vi_archive.person", f"VI:{token}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, label)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=party == "Independent",
                    )
                    if winner and declared:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="won" if primary else "elected",
                            seats=None,
                        )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
