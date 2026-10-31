import csv
import io
import json
import re
import struct
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import (
    special_2025,
    special_counties,
    special_files,
    special_fl,
    special_inbox,
    special_md,
    special_nc,
    special_nj,
    special_nm_1997,
    special_pdfs,
    special_wi_1993,
)
from ea.sources.us.elstats import GAZETTEER, MASSACHUSETTS, SUFFIXES, TENANTS, plain, url
from ea.sources.us.elstats import SOURCE as ARCHIVE
from ea.store import DATA, connect

SOURCE = "us.elstats_specials"
VERSION = "14"
SEARCHES = [
    ("NY", 2009),
    ("NY", 2011),
    ("NY", 2015),
    ("NY", 2020),
    ("NY", 2022),
    ("NY", 2024),
    ("NM", 2021),
    ("VA", 2023),
]
FLORIDA = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=1/11/2022&OfficialResults=Y"
LOUISIANA = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob={:%Y%m%d}/{}"
LOUISIANA_DAYS = [date(2013, 10, 19), date(2013, 11, 16), date(2021, 3, 20), date(2021, 4, 24)]
LOUISIANA_RUNOFFS = {date(2013, 11, 16), date(2021, 4, 24)}
CALIFORNIA = "https://elections.cdn.sos.ca.gov/special-elections/{}/{}-official-canvass.{}"
CALIFORNIA_FILES = [
    ("2020-cd25", "primary", "xls", date(2020, 3, 3)),
    ("2020-cd25", "general", "xls", date(2020, 5, 12)),
    ("2022-cd22", "primary", "xlsx", date(2022, 4, 5)),
    ("2022-cd22", "general", "xlsx", date(2022, 6, 7)),
]
TEXAS = "https://elections.sos.state.tx.us/elchist{}_race{}.htm"
TEXAS_PAGES = [
    (61, 90, date(1997, 3, 15), 28, False),
    (64, 90, date(1997, 4, 12), 28, True),
    (329, 89, date(2018, 6, 30), 27, False),
]
MASSACHUSETTS_SPECIALS = {
    106102: (date(2001, 10, 16), 9, ""),
    106100: (date(2001, 9, 25), 9, "Democratic"),
    106101: (date(2001, 9, 25), 9, "Republican"),
    103450: (date(2007, 10, 16), 5, ""),
    103437: (date(2007, 9, 4), 5, "Democratic"),
    103438: (date(2007, 9, 4), 5, "Green-Rainbow"),
    103439: (date(2007, 9, 4), 5, "Republican"),
    124836: (date(2013, 12, 10), 5, ""),
    124821: (date(2013, 10, 15), 5, "Democratic"),
    124822: (date(2013, 10, 15), 5, "Republican"),
}
STATES = {
    "MA": "Massachusetts",
    "SC": "South Carolina",
    "NY": "New York",
    "NM": "New Mexico",
    "VA": "Virginia",
    "FL": "Florida",
    "LA": "Louisiana",
    "CA": "California",
    "TX": "Texas",
    "OH": "Ohio",
    "GA": "Georgia",
    "UT": "Utah",
    "PA": "Pennsylvania",
    "IL": "Illinois",
    "NC": "North Carolina",
    "NJ": "New Jersey",
    "MD": "Maryland",
    "NE": "Nebraska",
    "KS": "Kansas",
    "MS": "Mississippi",
    "RI": "Rhode Island",
    "OK": "Oklahoma",
    "MT": "Montana",
    "WI": "Wisconsin",
    "SD": "South Dakota",
    "AZ": "Arizona",
    "TN": "Tennessee",
    "AR": "Arkansas",
    "OR": "Oregon",
}
OFFICIAL = {
    "SC": (
        "South Carolina Election Commission",
        "Official county results of the 2013 and 2017 special primaries",
        "https://www.enr-scvotes.org/SC/",
        [url for url, _ in special_inbox.SOUTH_CAROLINA.values()],
    ),
    "AR": (
        "Arkansas Secretary of State",
        "Certification report of the 2001 special primary",
        "https://www.sos.arkansas.gov/elections/research/election-results",
        [special_inbox.ARKANSAS],
    ),
    "OR": (
        "Oregon Secretary of State",
        "Official results of the 2011 special primary election",
        "https://sos.oregon.gov/elections/Pages/electionsstatistics.aspx",
        [special_inbox.OREGON],
    ),
    "AZ": (
        "Arizona Secretary of State",
        "Official canvasses of the 2012, 2018, and 2025 special primary elections",
        "https://azsos.gov/elections/election-information/2025-special-election",
        [
            special_2025.ARIZONA,
            *(
                special_inbox.ARIZONA_URL.format(day, name)
                for day, (name, *_) in special_inbox.ARIZONA_SPECIALS.items()
            ),
        ],
    ),
    "TN": (
        "Tennessee Secretary of State",
        "Official results of the October 7, 2025 special primary elections",
        "https://sos.tn.gov/elections/results",
        list(special_2025.TENNESSEE.values()),
    ),
    "SD": (
        "South Dakota Secretary of State",
        "Election history: official returns",
        "https://sdsos.gov/elections-voting/election-resources/election-history/default.aspx",
        [special_pdfs.SOUTH_DAKOTA],
    ),
    "NC": (
        "North Carolina State Board of Elections",
        "Election results data",
        "https://www.ncsbe.gov/results-data/election-results",
        [special_nc.link(day) for day in special_nc.DAYS],
    ),
    "MD": (
        "Maryland State Board of Elections",
        "Special election results",
        "https://elections.maryland.gov/elections/special_elections_past.html",
        [page for page, *_ in [*special_md.PAGES, *special_md.PRIMARIES]],
    ),
    "NJ": (
        "New Jersey Division of Elections",
        "Official special primary election results",
        "https://www.nj.gov/state/elections/election-information-2026.shtml",
        [url for url, *_ in special_nj.SPECIALS],
    ),
    "NE": (
        "Nebraska Secretary of State",
        "Board of State Canvassers official reports",
        "https://sos.nebraska.gov/previous-elections",
        [special_pdfs.NEBRASKA],
    ),
    "KS": (
        "Kansas Secretary of State",
        "Official election results",
        "https://www.sos.ks.gov/elections/election-results.html",
        [special_pdfs.KANSAS],
    ),
    "MS": (
        "Mississippi Secretary of State",
        "Certified special election results",
        "https://www.sos.ms.gov/elections-voting/election-results",
        special_counties.links()["MS"],
    ),
    "RI": (
        "Rhode Island Board of Elections",
        "Summary results reports",
        "https://elections.ri.gov/elections/results",
        special_counties.links()["RI"],
    ),
    "OK": (
        "Oklahoma State Election Board",
        "2002 election results",
        "https://oklahoma.gov/elections/elections-results/election-results/2002-election-results.html",
        special_counties.links()["OK"],
    ),
}
FIPS = {"GA": "13", "IL": "17", "KS": "20"}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Reform": "reform-party",
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LPF": "libertarian-party",
    "LIB": "libertarian-party",
    "LBT": "libertarian-party",
    "Green": "green-party",
    "Conservative": "NY:conservative-party",
    "Working Families": "NY:working-families-party",
    "Green-Rainbow": "MA:green-rainbow-party",
    "Independence": "NY:independence-party",
}
SUPERSEDED = (
    ("VA", date(2023, 2, 21)),
    ("NY", date(2015, 5, 5)),
    ("MA", date(2013, 12, 10)),
    ("MA", date(2007, 10, 16)),
)
MAIN = {"Democratic", "Republican", "DEM", "REP"}
INDEPENDENT = {"IND", "NOPTY", "NPTY", "NOP"}
WITHDREW = {
    "2001-12-11:OK:house:1:republican": {
        "Cathy Keating": "Withdrew from the January 8, 2002 runoff primary on December 14, 2001, so the special general "
        "was held that day instead (State Election Board, Candidates for Special Election, United States Representative "
        "District 1: https://oklahoma.gov/elections/candidates/candidate-filing-archives/cd01list.html)."
    }
}


def fetch() -> None:
    for state, year in SEARCHES:
        raw.fetch(ARCHIVE, url(TENANTS[state][0], year), suffix=".csv")
    raw.fetch(SOURCE, FLORIDA, suffix=".txt")
    links = [
        LOUISIANA.format(day, name)
        for day in LOUISIANA_DAYS
        for name in ["RacesCandidates_Multiparish.htm", "Votes_Multiparish.htm"]
        + [f"VotesParish/Votes_{parish:02}.htm" for parish in range(1, 65)]
    ]
    for election in MASSACHUSETTS_SPECIALS:
        raw.fetch(ARCHIVE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/", suffix=".csv")
    for special, stage, extension, _ in CALIFORNIA_FILES:
        raw.fetch(SOURCE, CALIFORNIA.format(special, stage, extension), suffix=f".{extension}")
    special_files.fetch_illinois()
    special_nc.fetch()
    special_md.fetch()
    special_pdfs.fetch()
    special_nj.fetch()
    special_fl.fetch()
    special_2025.fetch()
    special_inbox.fetch()
    special_counties.fetch()
    special_nm_1997.fetch()
    special_wi_1993.fetch()
    for election, race, _, _, _ in TEXAS_PAGES:
        raw.fetch(SOURCE, TEXAS.format(election, race), suffix=".htm")
    for link in links:
        try:
            raw.fetch(SOURCE, link, suffix=".json")
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise


def emit() -> Path:
    frames = []
    for state, year in SEARCHES:
        frame = pl.read_csv(raw.cached(ARCHIVE, url(TENANTS[state][0], year)).path, infer_schema=False)
        frames.append(
            frame.filter(
                pl.col("election_type").str.starts_with("Special")
                & pl.col("office_name").str.contains(
                    "(?i)^(representative in congress|united states representative|u\\.s\\. house)$"
                )
                & pl.col("division_type").is_in(["County", "Locality"])
            ).with_columns(state=pl.lit(state))
        )
    florida = [
        row
        for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, FLORIDA).path.read_text()), delimiter="\t")
        if row["RaceCode"] == "USR"
    ]
    frames.append(
        pl.DataFrame(
            {
                "state": ["FL"] * len(florida),
                "election_date": ["2022-01-11"] * len(florida),
                "district_name": [str(int(row["Juris1num"])) for row in florida],
                "candidate_name": [
                    "Write-ins"
                    if row["PartyCode"] == "WRI"
                    else " ".join(f"{row['CanNameFirst']} {row['CanNameMiddle']} {row['CanNameLast']}".split())
                    for row in florida
                ],
                "candidate_party_name": [
                    None if row["PartyCode"] in ("NPA", "WRI") else row["PartyCode"] for row in florida
                ],
                "division_name": [row["CountyName"] for row in florida],
                "votes": [row["CanVotes"] for row in florida],
            }
        )
    )
    louisiana: list[dict[str, object]] = []
    with connect() as conn:
        parishes = [
            text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '22%' order by n.text"""
            ).fetchall()
        ]
    for day in LOUISIANA_DAYS:
        races = json.loads(
            raw.cached(SOURCE, LOUISIANA.format(day, "RacesCandidates_Multiparish.htm")).path.read_text()
        )["Races"]["Race"]
        totals = {}
        for race in json.loads(raw.cached(SOURCE, LOUISIANA.format(day, "Votes_Multiparish.htm")).path.read_text())[
            "Races"
        ]["Race"]:
            for choice in race["Choice"]:
                totals[(race["ID"], choice["ID"])] = (int(choice["VoteTotal"]), choice["Outcome"])
        counted: dict[tuple[str, str], int] = {}
        for race in races if isinstance(races, list) else [races]:
            found = re.match(r"^U\. S\. Representative -- (\d+)\w\w Congressional District$", race["SpecificTitle"])
            if found is None:
                continue
            choices = {choice["ID"]: choice["Desc"] for choice in race["Choice"]}
            for index, parish in enumerate(parishes):
                try:
                    listed = json.loads(
                        raw.cached(
                            SOURCE, LOUISIANA.format(day, f"VotesParish/Votes_{index + 1:02}.htm")
                        ).path.read_text()
                    )["Races"]
                except LookupError:
                    continue
                for cell in listed["Race"] if isinstance(listed["Race"], list) else [listed["Race"]]:
                    if cell["ID"] != race["ID"]:
                        continue
                    for choice in cell["Choice"]:
                        match = re.match(r"^(.*?)\s*\(([A-Z]+)\)$", choices[choice["ID"]].strip())
                        counted[(race["ID"], choice["ID"])] = counted.get((race["ID"], choice["ID"]), 0) + int(
                            choice["VoteTotal"]
                        )
                        louisiana.append(
                            {
                                "state": "LA",
                                "election_date": str(day),
                                "district_name": found[1],
                                "candidate_name": (match[1] if match else choices[choice["ID"]]).strip(),
                                "candidate_party_name": None if match is None or match[2] in INDEPENDENT else match[2],
                                "division_name": parish,
                                "votes": str(choice["VoteTotal"]),
                                "outcome": totals[(race["ID"], choice["ID"])][1],
                                "runoff": day in LOUISIANA_RUNOFFS,
                            }
                        )
            for choice_id, desc in choices.items():
                if counted.get((race["ID"], choice_id), 0) != totals[(race["ID"], choice_id)][0]:
                    raise ValueError(f"{day} {desc}: the parishes do not sum to the statewide total")
    frames.append(pl.DataFrame(louisiana, infer_schema_length=None))
    with zipfile.ZipFile(raw.cached(ARCHIVE, GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "MA")
    massachusetts_towns: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        massachusetts_towns.setdefault(plain(name), geoid[:5])
        massachusetts_towns.setdefault(plain(re.sub(SUFFIXES, "", name.lower())), geoid[:5])
    massachusetts: list[dict[str, object]] = []
    for election, (day, district, primary) in MASSACHUSETTS_SPECIALS.items():
        table = pl.read_csv(
            raw.cached(ARCHIVE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/").path,
            infer_schema=False,
            has_header=False,
        ).rows()
        names, parties = table[0], table[1]
        tallies: dict[tuple[str, str | None, str], int] = {}
        for row in table[2:]:
            if row[0] in (None, "TOTALS"):
                continue
            code = massachusetts_towns.get(
                plain(
                    re.sub(
                        r"^([NSEW])\. ",
                        lambda hit: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[hit[1]],
                        row[0],
                    )
                )
            )
            if code is None:
                raise ValueError(f"{election}: no town for {row[0]}")
            for column in range(3, len(row)):
                if names[column] in (None, "Blanks", "Total Votes Cast") or row[column] is None:
                    continue
                key = (
                    "Write-ins" if names[column] == "All Others" else names[column],
                    parties[column],
                    code,
                )
                tallies[key] = tallies.get(key, 0) + int(row[column].replace(",", ""))
        massachusetts.extend(
            {
                "state": "MA",
                "election_date": str(day),
                "district_name": str(district),
                "candidate_name": name,
                "candidate_party_name": party,
                "division_name": f"code:{code}",
                "votes": str(votes),
                "primary": primary,
            }
            for (name, party, code), votes in tallies.items()
        )
    frames.append(pl.DataFrame(massachusetts, infer_schema_length=None))
    california: list[dict[str, object]] = []
    for special, stage, extension, day in CALIFORNIA_FILES:
        sheet = pl.read_excel(
            raw.cached(SOURCE, CALIFORNIA.format(special, stage, extension)).path,
            has_header=False,
            engine="calamine",
            infer_schema_length=0,
        ).rows()
        district = int(re.match(r"^(\d+)", next(row[0] for row in sheet if row[0] and "District" in row[0]))[1])
        cells: list[dict[str, object]] = []
        for index, row in enumerate(sheet):
            if row[0] == "County**":
                break
            if (
                row[0] is None
                and row[1] is None
                and row[2] is not None
                and (sheet[index - 1][2] is None or (sheet[index - 1][0] or "").startswith("Percentage"))
                and sheet[index + 3][0] not in (None, "Total Votes Cast")
            ):
                block = []
                for line in sheet[index + 3 :]:
                    if line[0] is None or line[0].startswith(("Total", "Votes", "Percentage")):
                        break
                    block.append(line)
                for column in range(2, len(row), 2):
                    if row[column] is None:
                        continue
                    name = f"{row[column]} {sheet[index + 1][column]}"
                    party = sheet[index + 2][column].upper()
                    total = sum(int(line[column]) for line in block)
                    cells.extend(
                        {"name": name, "party": party, "county": line[0], "votes": int(line[column]), "total": total}
                        for line in block
                    )
        ranked = sorted({cell["name"]: cell["total"] for cell in cells}.items(), key=lambda item: -item[1])
        leaders = {name for name, _ in ranked[: 1 if stage == "general" else 2]}
        for cell in cells:
            california.append(
                {
                    "state": "CA",
                    "election_date": str(day),
                    "district_name": str(district),
                    "candidate_name": cell["name"],
                    "candidate_party_name": cell["party"],
                    "division_name": cell["county"],
                    "votes": str(cell["votes"]),
                    "outcome": ("Elected" if stage == "general" else "Runoff")
                    if cell["name"] in leaders
                    else "Defeated",
                    "runoff": stage == "general",
                }
            )
    frames.append(pl.DataFrame(california, infer_schema_length=None))
    texas: list[dict[str, object]] = []
    for election, race, day, district, runoff in TEXAS_PAGES:
        rows = [
            [
                re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", cell)).strip()
                for cell in re.findall(r"<T[HD][^>]*>(.*?)</T[HD]>", row, re.DOTALL | re.IGNORECASE)
            ]
            for row in re.findall(
                r"<tr[^>]*>(.*?)</tr>",
                raw.cached(SOURCE, TEXAS.format(election, race)).path.read_text(errors="replace"),
                re.DOTALL | re.IGNORECASE,
            )
        ]
        firsts, lasts, parties = rows[0], rows[1], rows[2]
        stop = parties.index("Votes")
        cells = []
        for row in rows[4:]:
            for column in range(1, stop):
                name = (
                    re.sub(r",? (III|Jr\.|Sr\.)$", "", f"{firsts[column]} {lasts[column]}")
                    if parties[column] != "W-I"
                    else "Write-ins"
                )
                cells.append(
                    {
                        "name": name,
                        "party": parties[column],
                        "county": row[0].title(),
                        "votes": int(row[column].replace(",", "")),
                    }
                )
        by_name: dict[str, int] = {}
        for cell in cells:
            by_name[cell["name"]] = by_name.get(cell["name"], 0) + cell["votes"]
        ranked = sorted(
            ((name, count) for name, count in by_name.items() if name != "Write-ins"), key=lambda item: -item[1]
        )
        majority = ranked[0][1] * 2 > sum(by_name.values())
        leaders = {name for name, _ in ranked[: 1 if runoff or majority else 2]}
        for cell in cells:
            texas.append(
                {
                    "state": "TX",
                    "election_date": str(day),
                    "district_name": str(district),
                    "candidate_name": cell["name"],
                    "candidate_party_name": None if cell["party"] in ("W-I", "IND") else cell["party"],
                    "division_name": cell["county"],
                    "votes": str(cell["votes"]),
                    "outcome": ("Elected" if runoff or majority else "Runoff")
                    if cell["name"] in leaders
                    else "Defeated",
                    "runoff": runoff,
                }
            )
    frames.append(pl.DataFrame(texas, infer_schema_length=None))
    frames.append(pl.DataFrame(special_files.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_files.illinois_rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_files.illinois_primary_rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_nc.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_md.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_pdfs.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_nj.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_fl.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_2025.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_inbox.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_counties.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_nm_1997.rows(), infer_schema_length=None))
    frames.append(pl.DataFrame(special_wi_1993.rows(), infer_schema_length=None))
    frame = pl.concat(frames, how="diagonal").with_columns(
        day=pl.col("election_date").str.slice(0, 10).str.to_date(),
        runoff=pl.col("runoff").fill_null(False),
        primary=pl.col("primary").fill_null(""),
        district=pl.col("district_name").cast(pl.Int32),
        votes=pl.col("votes").cast(pl.Int64),
    )
    for state, day in SUPERSEDED:
        frame = frame.filter(~((pl.col("state") == state) & (pl.col("day") == day)))
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{plain(text)}city"] = code
            names.setdefault(plain(text), code)
        counties["SD"][plain("Shannon")] = "46102"
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="U.S. House special elections in Florida, New York, New Mexico, and Virginia by county from the state "
            "election archives",
            polity="US",
        )
        house = cs.key("us.body", "house")
        fptp = cs.key("us.electoral_system", "fptp")
        two_round = cs.key("us.electoral_system", "two_round")
        top_two = cs.key("us.electoral_system", "top_two")
        sources: dict[str, UUID] = {}
        for state in STATES:
            if state == "MA":
                sources[state] = cs.source("Massachusetts Secretary of the Commonwealth", "Election statistics archive")
                if sources[state] in cs.minted:
                    raise ValueError("MA: the archive's source must already exist")
                for election in MASSACHUSETTS_SPECIALS:
                    cs.artifact(
                        raw.cached(ARCHIVE, f"{MASSACHUSETTS}download/{election}/precincts_include:0/"), sources[state]
                    )
                continue
            if state == "CA":
                publisher, title = "California Secretary of State", "Special election official canvasses"
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url="https://www.sos.ca.gov/elections/prior-elections/special-elections",
                        license="Public record",
                    )
                for special, stage, extension, _ in CALIFORNIA_FILES:
                    cs.artifact(raw.cached(SOURCE, CALIFORNIA.format(special, stage, extension)), sources[state])
                continue
            if state in special_files.SITES:
                publisher, title, site = special_files.SITES[state]
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=site,
                        license="Public record",
                    )
                for owner, link in special_files.STORED:
                    if owner == state:
                        cs.artifact(raw.cached(SOURCE, link), sources[state])
                if state == "OH":
                    for day, files in special_inbox.OHIO.items():
                        for name in files.values():
                            cs.artifact(raw.cached(SOURCE, special_inbox.OHIO_URL.format(day, name)), sources[state])
                    for day, (_, _, files) in special_inbox.OHIO_SPECIALS.items():
                        for name in files.values():
                            cs.artifact(
                                raw.cached(
                                    SOURCE, special_inbox.OHIO_URL.format(day, name).replace("MF-290", "MF-293")
                                ),
                                sources[state],
                            )
                if state == "UT":
                    cs.artifact(raw.cached(SOURCE, special_inbox.UTAH_URL), sources[state])
                if state == "WI":
                    cs.artifact(raw.cached(SOURCE, special_pdfs.WISCONSIN), sources[state])
                    cs.artifact(raw.cached(SOURCE, special_inbox.WISCONSIN), sources[state])
                continue
            if state in OFFICIAL:
                publisher, title, site, links = OFFICIAL[state]
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=site,
                        license="Public record",
                    )
                for link in links:
                    cs.artifact(raw.cached(SOURCE, link), sources[state])
                continue
            if state == "TX":
                publisher, title = "Texas Secretary of State", "Election history: official results"
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url="https://elections.sos.state.tx.us/index.htm",
                        license="Public record",
                    )
                for election, race, _, _, _ in TEXAS_PAGES:
                    cs.artifact(raw.cached(SOURCE, TEXAS.format(election, race)), sources[state])
                continue
            if state == "LA":
                publisher, title = "Louisiana Secretary of State", "Official election results"
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    raise ValueError("LA: the Secretary of State's source must already exist")
                for day in LOUISIANA_DAYS:
                    for name in ["RacesCandidates_Multiparish.htm", "Votes_Multiparish.htm"] + [
                        f"VotesParish/Votes_{parish:02}.htm" for parish in range(1, 65)
                    ]:
                        try:
                            cs.artifact(raw.cached(SOURCE, LOUISIANA.format(day, name)), sources[state])
                        except LookupError:
                            pass
                continue
            if state == "FL":
                publisher, title = (
                    "Florida Division of Elections",
                    "Official results extracts, special elections",
                )
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=FLORIDA,
                        license="Public record",
                    )
                cs.artifact(raw.cached(SOURCE, FLORIDA), sources[state])
                for primaries in special_fl.PRIMARIES:
                    cs.artifact(raw.cached(SOURCE, primaries), sources[state])
                continue
            _, publisher, _ = TENANTS[state]
            sources[state] = cs.source(publisher, "Election statistics archive")
            if sources[state] in cs.minted:
                raise ValueError(f"{state}: the archive's source must already exist")
            for year in frame.filter(pl.col("state") == state)["election_date"].str.slice(0, 4).unique():
                cs.artifact(raw.cached(ARCHIVE, url(TENANTS[state][0], int(year))), sources[state])

        canvass_pages: dict[tuple[str, int], UUID] = {}
        scan_sources: dict[tuple[str, date], UUID] = {}
        for state, day, link, publisher, title, site, rows in (
            (
                "NM",
                date(1997, 5, 13),
                special_nm_1997.CANVASS,
                "New Mexico Secretary of State",
                "1997 special election canvass",
                "https://sos.nm.gov/sos-archive/election-results-archive/1997-election-results/",
                special_nm_1997.rows(),
            ),
            (
                "WI",
                date(1993, 5, 4),
                special_wi_1993.PAGE,
                "Wisconsin Legislative Reference Bureau",
                "Wisconsin Blue Book 1993-1994",
                special_wi_1993.PAGE,
                special_wi_1993.rows(),
            ),
        ):
            canvass = raw.cached(SOURCE, link)
            official = cs.source(publisher, title)
            if official in cs.minted:
                cs.add(
                    "source",
                    id=official,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=site,
                    license="Public record",
                )
            scan_sources[(state, day)] = official
            canvass_artifact = cs.artifact(canvass, official)
            for number in sorted({row["page"] for row in rows}):
                image = DATA / "pages" / SOURCE / f"{canvass.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(canvass.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                canvass_pages[(state, number)] = cs.page(canvass_artifact, number)
                cs.add(
                    "source_page",
                    id=canvass_pages[(state, number)],
                    artifact_id=canvass_artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )

        created: set[UUID] = set()
        elections: set[UUID] = set()
        contests: dict[str, UUID] = {}
        for (state, day, district, runoff, primary), lines in frame.sort("day").group_by(
            "state", "day", "district", "runoff", "primary", maintain_order=True
        ):
            label = STATES[state]
            key = (
                f"{day}:{state}:house:{district}:{primary.lower()}" + (":runoff" if runoff else "")
                if primary
                else f"{day}:{state}:house:{district}:special" + (":runoff" if runoff else "")
            )
            concurrent = (
                bool(primary)
                and not runoff
                and conn.execute(
                    """select exists (select 1 from external_id where scheme = 'us.contest' and value = %(key)s)
                       and not exists (select 1 from external_id where scheme = %(scheme)s and value = %(key)s)""",
                    {"key": key, "scheme": f"{SOURCE}.result_set"},
                ).fetchone()[0]
            )
            if concurrent:
                key += ":special"
            general = conn.execute(
                """select ct.id, ct.district_area_id, ct.boundary_set_id from external_id e
                   join contest ct on ct.id = e.entity_id
                   where e.scheme = 'us.contest' and e.value ~ %s and left(e.value, 10) < %s
                   order by e.value desc limit 1""",
                [f"^\\d{{4}}-11-\\d\\d:{state}:house:{district}$", str(day)],
            ).fetchone()
            if general is None:
                raise ValueError(f"{key}: no earlier general for the district")
            stage = "special-primary" if concurrent else "primary" if primary else "runoff" if runoff else "special"
            election = cs.key("us.election", f"{day}:{state}:{stage}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="primary" if primary else stage,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day:%B} {day.day}, {day.year} {label} special {'primary runoff' if primary and runoff else 'primary' if primary else 'runoff' if runoff else 'election'}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-{stage}", entity_id=election)
            contest = cs.key("us.contest", key)
            contests[key] = contest
            ordinal = (
                "at-large"
                if district == 0
                else f"{district}{'th' if district % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(district % 10, 'th')}"
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=house,
                district_area_id=general[1],
                seat_id=None,
                term="remainder",
                boundary_set_id=general[2],
                party_scope_id=cs.key("us.party", PARTIES[primary]) if primary else None,
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=next(
                    (
                        previous
                        for earlier, previous in reversed(contests.items())
                        if earlier.endswith(f":{state}:house:{district}:{primary.lower() or 'special'}")
                    ),
                    None,
                )
                if runoff
                else None,
                electoral_system_id={
                    "LA": two_round,
                    "TX": two_round,
                    "GA": two_round,
                    "MS": two_round,
                    "AR": two_round,
                    "CA": top_two,
                }.get(state, fptp),
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} U.S. House special {primary + ' primary' + (' runoff' if runoff else '') if primary else 'runoff' if runoff else 'election'} in {label}'s {ordinal} congressional district",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{day}-{stage}/house-{'at-large' if district == 0 else district}-{primary.lower() or 'special'}{'-special' if concurrent else ''}",
                entity_id=contest,
            )

            candidates: dict[str, tuple[str | None, int]] = {}
            writein = 0
            pseudo = re.compile(r"(?i)^(blank|void|total (votes|ballots)( cast)?|undervotes|overvotes)$")
            for name, party, votes in (
                lines.group_by("candidate_name", "candidate_party_name")
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
                .iter_rows()
            ):
                if pseudo.match(name):
                    continue
                if re.match(r"(?i)^(scattering|write-?ins?)$", name):
                    writein += votes
                    continue
                previous = candidates.get(name)
                if previous is None:
                    candidates[name] = (party, votes)
                else:
                    candidates[name] = (
                        previous[0] if previous[0] in MAIN else party if party in MAIN else previous[0],
                        previous[1] + votes,
                    )
            outcomes = dict(lines.group_by("candidate_name").agg(pl.col("outcome").first()).iter_rows())
            if state in ("LA", "CA", "TX", "GA", "MS", "AR"):
                winners = {name for name, outcome in outcomes.items() if outcome == "Elected"}
            else:
                winners = {max(candidates, key=lambda candidate: candidates[candidate][1])}

            known = {
                slug(text): person
                for text, person in conn.execute(
                    """select distinct n.text, c.person_id from candidacy c
                       join contest_option o on o.id = c.option_id join contest ct on ct.id = o.contest_id
                       join name n on n.entity_id = c.person_id and n.is_preferred
                       where ct.district_area_id = %s""",
                    [general[1]],
                ).fetchall()
            }
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options: list[tuple[UUID, dict[str, int], dict[str, tuple[int, list[float]]]]] = []
            for name, (party, _) in candidates.items():
                if name in winners or outcomes.get(name) == "Runoff":
                    row = conn.execute(
                        """select t.person_id from tenure t join name n on n.entity_id = t.person_id and n.is_preferred
                           where t.body_id = %s and t.from_date >= %s and t.from_date < %s::date + 200
                           and n.text ilike %s order by t.from_date limit 1""",
                        [house, day, day, f"%{name.split()[-1].removesuffix(' Jr.').removesuffix(' Sr.')}%"],
                    ).fetchone()
                    if row is None:
                        if name in winners and day.year < 2026 and not primary:
                            raise ValueError(f"{key}: no House tenure for {name}")
                        person = known.get(slug(name)) or cs.key(f"{SOURCE}.person", f"{state}:{slug(name)}")
                        if person in cs.minted and person not in created:
                            created.add(person)
                            cs.add("person", id=person)
                            cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                    else:
                        person = row[0]
                else:
                    person = known.get(slug(name)) or cs.key(f"{SOURCE}.person", f"{state}:{slug(name)}")
                    if person in cs.minted and person not in created:
                        created.add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                party_id = cs.key("us.party", PARTIES[party]) if party in PARTIES else None
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    is_independent=party_id is None,
                    is_incumbent=False,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="withdrew"
                    if name in WITHDREW.get(key, {})
                    else ("won" if primary else "elected")
                    if name in winners
                    else "advanced"
                    if outcomes.get(name) == "Runoff"
                    else "lost",
                    notes=WITHDREW.get(key, {}).get(name),
                )
                by_unit = {
                    str(division): int(count)
                    for division, count in lines.filter(pl.col("candidate_name") == name)
                    .group_by("division_name")
                    .agg(pl.col("votes").sum())
                    .iter_rows()
                }
                located = (
                    {
                        str(division): (int(page), list(box))
                        for division, page, box in lines.filter(
                            (pl.col("candidate_name") == name) & pl.col("page").is_not_null()
                        )
                        .select("division_name", "page", "box")
                        .iter_rows()
                    }
                    if "page" in lines.columns
                    else {}
                )
                options.append((option, by_unit, located))
            write_in = cs.key("us.option", f"{key}:write_in")
            cs.add("contest_option", id=write_in, contest_id=contest, kind="write_in", ballot_label="Write-ins")
            options.append(
                (
                    write_in,
                    {
                        str(division): int(count)
                        for division, count in lines.filter(
                            pl.col("candidate_name").str.contains("(?i)^(scattering|write-?ins?)$")
                        )
                        .group_by("division_name")
                        .agg(pl.col("votes").sum())
                        .iter_rows()
                    },
                    {},
                )
            )
            prior = conn.execute(
                """select id, election_id, contest_id, stage, kind, source_id, as_of, completeness
                   from result_set where contest_id = %s and is_default and id <> %s""",
                [contest, result_set],
            ).fetchone()
            if prior is not None:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            ("id", "election_id", "contest_id", "stage", "kind", "source_id", "as_of", "completeness"),
                            prior,
                            strict=True,
                        )
                    ),
                    status="superseded",
                    is_default=False,
                )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=scan_sources.get((state, day), sources[state]),
                is_default=True,
                completeness=1,
                supersedes_id=prior[0] if prior is not None else None,
            )
            totals: dict[str, int] = {}
            for option, by_unit, located in options:
                for division, count in by_unit.items():
                    simple = plain(
                        re.sub(r"\s*\(.*\)$", "", division).lower().replace("saint ", "st ").replace("&", "and")
                    )
                    district_total = division.startswith("cd:")
                    code = (
                        f"{FIPS[state]}{district:02d}"
                        if district_total
                        else division.removeprefix("code:")
                        if division.startswith("code:")
                        else counties[state].get(re.sub(r"(county|parish|borough)$", "", simple))
                        or counties[state].get(simple)
                    )
                    if code is None:
                        raise ValueError(f"{key}: no county for {division}")
                    unit = cs.unit("us.cd" if district_total else "us.county", code, day)
                    totals[unit] = totals.get(unit, 0) + count
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
                    if division in located:
                        page, box = located[division]
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=1,
                            page_id=canvass_pages[(state, page)],
                            source_id=None,
                            x0=box[0],
                            y0=box[1],
                            x1=box[2],
                            y1=box[3],
                        )
            for unit, total in totals.items():
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
