from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wa_county_2026"
VERSION = "1"
DAY = date(2026, 11, 3)
MANUAL = DATA / "inbox" / "manual" / "MF-385"
KING = ("King County Elections", "Certified results, August 4, 2026 primary", "https://kingcounty.gov/elections", "results.pdf")
PIERCE = (
    "Pierce County Auditor",
    "Final results, August 4, 2026 primary",
    "https://www.piercecountywa.gov/328/Elections",
    "Final Results for Primary2026 FINAL_202609171458102314.pdf",
)
WHATCOM = (
    "Whatcom County Auditor",
    "Sample ballot, November 3, 2026 general election",
    "https://www.whatcomcounty.us/1574",
    "11.03.26 Sample Ballot_202609231409027439.pdf",
)
CONTESTS = (
    (KING, "53033", "Northeast Electoral District Judge Position 1", "judge-position-1", False, ("Jan Trasen", "Bianca Tse")),
    (KING, "5363000", "Council District 5", "council-district-5", True, ("Nilu Jenks", "Julie Kang")),
    (KING, "5363000", "Municipal Court Judge Position 5", "municipal-court-judge-position-5", False, ("Gabe Rothstein", "Garmon Newsom")),
    (PIERCE, "53053", "District Court Position 7", "district-court-position-7", False, ("Pam Nogueira", "Mike Sommerfeld")),
    (WHATCOM, "53073", "District Court Judge Position 1", "district-court-judge-position-1", False, ("Angela Anderson",)),
    (WHATCOM, "53073", "District Court Judge Position 2", "district-court-judge-position-2", False, ("Jonathan Rands",)),
)


def fetch() -> None:
    for _, _, url, name in {source for source, *_ in CONTESTS}:
        path = MANUAL / name
        raw.store(
            SOURCE,
            url,
            path.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def emit() -> Path:
    with connect() as conn:
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/wa/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington county and city judge and council races missing from the November 3, 2026 ballot, from the August primary results and the Whatcom sample ballot",
            polity="US-WA",
        )
        system = cs.key("us.electoral_system", "fptp")
        sources: dict[str, UUID] = {}
        kept = 0
        for (publisher, title, url, name), code, office_title, tail, remainder, people in CONTESTS:
            if url not in sources:
                source = sources[url] = cs.source(publisher, title)
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url, license="Public record")
                cs.artifact(raw.cached(SOURCE, url), source)
            source = sources[url]
            election = cs.key("us.election", f"{DAY}:WA:{code}")
            if election in cs.minted:
                print("no election:", code)
                continue
            election_path = next(path for path, entity in slugs.items() if entity == election and path.endswith(f"/{DAY}-general"))
            polity_path = election_path.removesuffix(f"/{DAY}-general")
            polity = slugs[polity_path]
            place = conn.execute("select text from name where entity_id = %s and is_preferred and lang = 'en'", [polity]).fetchone()[0]
            full = f"{place} {office_title}"
            owner = cs.key("us.office", f"WA:{code}:{tail}")
            contest_key = f"{DAY}:WA:{code}:{tail}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{election_path}/{tail}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            if owner in cs.minted:
                cs.add("office", id=owner, polity_id=polity, kind="other")
                cs.add("name", entity_id=owner, text=full, lang="en", kind="official", is_preferred=True)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if remainder else "full",
            )
            cs.add("name", entity_id=contest, text=f"{DAY.year} {full} election", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            for ballot in people:
                token = slug(ballot)
                person = cs.key(f"{SOURCE}.person", f"{contest_key}:{token}")
                option = cs.key("us.option", f"{contest_key}:{token}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                candidacy = cs.key("us.candidacy", f"{contest_key}:{token}:head")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=True,
                )
                cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                kept += 1
        print(f"{kept} candidates")
        return cs.write()
