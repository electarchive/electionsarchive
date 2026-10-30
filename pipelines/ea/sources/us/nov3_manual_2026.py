import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.nov3_manual_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Official county ballot measures and sample ballots, November 3, 2026 election"
SCHOOLS = {
    ("TX", "Georgetown Independent School District"): "4820600",
    ("CA", "Fallbrook Union Elementary School District"): "0613500",
    ("CA", "Julian Union High School District"): "0619110",
    ("CA", "Galt Joint Union High School District"): "0614820",
    ("CA", "Jefferson Union High School District"): "0618930",
}
MANUAL = DATA / "inbox" / "manual"
ROWS_FILE = Path(__file__).with_name("nov3_manual_2026.json")
COUNTIES = {
    "35001": ("NM", "https://www.bernco.gov/public-works/capital-improvement-program/general-obligation-bonds/", "MF-387/bernalillo/bernalillo-general-obligation-bonds.html"),
    "05007": ("AR", "https://bentoncountyar.gov/election-commission/", "MF-387/benton-ar/benton-ar-election-commission.html"),
    "04013": ("AZ", "https://elections.maricopa.gov/candidates/county-ballot-measures.html", "MF-387/maricopa/maricopa-county-ballot-measures.html"),
    "04019": ("AZ", "https://www.recorder.pima.gov/PressRelease/2026/11.3%20General%20Election%20What%20Voters%20Need%20to%20Know.pdf", "MF-387/pima/11.3 General Election What Voters Need to Know.pdf"),
    "04021": ("AZ", "https://www.pinal.gov/1847/2026-General-Election-Sample-Ballots", "MF-387/pinal/pinal-2026-general-election-sample-ballots.html"),
    "45019": ("SC", "https://vote.charlestoncounty.gov/", "MF-390/sc-charleston-2026-11-03-local-questions.html"),
    "45043": ("SC", "https://www.gtcountysc.gov/upcoming-elections/", "MF-390/22110326_abs_sample (3).pdf"),
    "13035": ("GA", "https://www.buttscounty.org/elections", "MF-390/ga-butts-2026-11-03-referendum-notice.html"),
    "13255": ("GA", "https://www.spaldingcountyelections.com/", "MF-390/RESOLUTION-CORRECTED-WITH-BOER-CHAIR-SIGNATURE.pdf"),
    "31055": ("NE", "https://www.votedouglascounty-ne.gov/", "MF-390/GN26SampleBallot.pdf"),
    "31109": ("NE", "https://www.lancaster.ne.gov/", "MF-390/LCEC General Sample Ballot.pdf"),
    "31079": ("NE", "https://files.hallcountyne.gov/", "MF-390/26GNEHALL_7_PUB.pdf"),
    "48135": ("TX", "https://www.ectorcountytx.gov/271/Elections", "MF-392/ector/Pct. 201, 202, 203, 204.3, 206.5, 209_ybmswlfq61.pdf"),
    "04023": ("AZ", "https://www.nogalesaz.gov/336/Proposition-418---Alternative-Expenditur", "MF-404/nogales-az-2026-general-proposition-418.html"),
    "06059": ("CA", "https://ocvote.org/elections/2026-general-election-measures-on-the-ballot", "MF-nov3-ca/orange/notice-of-election-nov2026.pdf"),
    "06073": ("CA", "https://www.sdvote.com/content/dam/rov/en/election/nov-3-2026-gubernatorial-general-election/argument-deadline-measures.pdf", "MF-nov3-ca/san-diego/argument-deadline-measures.pdf"),
    "06067": ("CA", "https://elections.saccounty.gov/content/vre/us/en/election-information/current-measure-information.html", "MF-nov3-ca/sacramento/current-measure-information.html"),
    "06081": ("CA", "https://www.smcacre.gov/elections/november-3-2026-statewide-general-election", "MF-nov3-ca/san-mateo/county-amendments-resolutions.pdf"),
    "04015": ("AZ", "https://www.bullheadcityaz.gov/leadership-government/elections/", "bullhead/publicity-pamphlet-26.pdf"),
    "48209": ("TX", "https://www.hayscountytx.gov/elections", "MF-392/hays/Hays County Combined Sample Ballot 11x17.pdf"),
    "48453": ("TX", "https://countyclerk.traviscountytx.gov/elections/", "../y26/tx/travis.pdf"),
    "48167": ("TX", "https://www.galvestoncountytx.gov/our-county/county-clerk/elections", "MF-392/galveston/November-2026-General-Election-Ballot-and-Measures.pdf"),
    "48201": ("TX", "https://www.harrisvotes.com/", "../y26/tx/harris_sample.pdf"),
    "48085": ("TX", "https://www.collincountytx.gov/Elections", "../y26/tx/collin.pdf"),
    "48491": ("TX", "https://www.wilcotx.gov/elections", "MF-392/williamson/20261103 November JGSE - COMPREHENSIVE BALLOT.pdf")
}


def fetch() -> None:
    for _, url, file in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / file
            suffix = path.suffix
            raw.store(SOURCE, url, path.read_bytes(), suffix=suffix, retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC), media_type="application/pdf" if suffix == ".pdf" else "text/html")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = [
        {"county": row["county_fips"], "state": row["state"], "publisher": row["publisher"], "entity": row["place"], "label": f"{row['number']}: {row['title']}" if row["number"] is not None else row["title"], "wording": row["text"]}
        for row in json.loads(ROWS_FILE.read_text())
    ]
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Local referenda on the November 3, 2026 ballot in Arizona, New Mexico, Arkansas, Georgia, South Carolina, Nebraska, Texas, and California, with official wording",
            polity="US",
        )
        civils = {state: civmint.Civil(conn, cs, fips[:2], state) for fips, (state, _, _) in COUNTIES.items()}
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, publisher in frame.select("county", "publisher").unique("county").iter_rows():
            _, url, _ = COUNTIES[county]
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            state = row["state"]
            civil = civils[state]
            found = civil.found
            parent = cs.key("us.election", f"{DAY}:{state}")
            url = COUNTIES[row["county"]][1]
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["county"]]
            option = None
            if re.match(r"(?i)(city|town|village) of ", entity):
                option = civil.place(re.sub(r"(?i)^(city|town|village) of ", "", entity))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif entity.endswith(" Township"):
                option = civil.town(entity, row["county"][:5])
            elif (state, entity) in SCHOOLS:
                option = f"us.school_district:{SCHOOLS[state, entity]}"
                if option not in found:
                    option = civil.mint("us.school_district", SCHOOLS[state, entity], entity)
            elif re.search(r"School District|CUSD", entity):
                option = civil.school(entity)
            elif f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(conn, cs, state, (county, county_area, county_path, county_code), entity, civil.slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = civil.slugs.get(f"{path}/{DAY}-general") or cs.key("us.election", f"{DAY}:{state}:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add("name", entity_id=election, text=f"{DAY.year} {place} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            contest_key = f"{DAY}:{state}:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if civil.slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            civil.slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=url,
                source_id=source,
            )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
