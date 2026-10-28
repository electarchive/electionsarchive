import html
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.calendar_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LA_CANDIDATES = "https://voterportal.sos.la.gov/CandidateInquiry/StatewideCandidate/CandidateList"
SOURCES = {
    "guam": (
        "https://www.govinfo.gov/link/uscode/48/1422?type=usc&year=mostrecent&link-type=html",
        "official_body",
        "U.S. Government Publishing Office (govinfo)",
        "48 U.S.C. § 1422: Governor and Lieutenant Governor of Guam (runoff on the fourteenth day after a general election no ticket wins by a majority)",
    ),
    "GA": (
        "https://web.archive.org/web/2026id_/https://sos.ga.gov/sites/default/files/forms/2026%20Elections%20Calendar_0.pdf",
        "official_body",
        "Georgia Secretary of State",
        "2026 Elections Calendar",
    ),
    "MS": (
        "https://www.sos.ms.gov/content/documents/elections/2026%20Election%20Calendar.pdf",
        "official_body",
        "Mississippi Secretary of State",
        "2026 Election Calendar",
    ),
    "AR": (
        "https://www.sos.arkansas.gov/uploads/elections/2026_Election_Calendar_Rev._7-2026_.pdf",
        "official_body",
        "Arkansas Secretary of State",
        "2026 Election Calendar (revised July 2026)",
    ),
    "LA": (
        "https://www.sos.la.gov/media/p31j4zkc/elections-calendar-2026.pdf",
        "official_body",
        "Louisiana Secretary of State",
        "2026 Elections calendar",
    ),
    "LA-2027": (
        "https://www.sos.la.gov/media/byfpyc5f/elections-calendar-2027.pdf",
        "official_body",
        "Louisiana Secretary of State",
        "2027 Elections calendar (revised October 2026)",
    ),
    "LA-92": (
        "https://www.house.louisiana.gov/H_Misc/proclamations/District%2092%20-%20Proclamation.pdf",
        "official_body",
        "Louisiana House of Representatives",
        "Proclamation of the Speaker: special election for the 92nd Representative District",
    ),
    "MS-70-77": (
        "https://governorreeves.ms.gov/governor-tate-reeves-sets-two-special-elections/",
        "official_body",
        "Office of Governor Tate Reeves",
        "Governor Tate Reeves Sets Two Special Elections (House Districts 70 and 77)",
    ),
    "MS-34": (
        "https://www.mississippifreepress.org/mississippi-governor-sets-special-election-after-state-senator-retires/",
        "news_agency",
        "Mississippi Free Press",
        "Mississippi Governor Sets Special Election After State Senator Retires",
    ),
    "AR-18": (
        "https://governor.arkansas.gov/news_post/sanders-calls-for-special-election-to-fill-vacancy-in-office-for-state-senator-for-district-18-2/",
        "official_body",
        "Office of Governor Sarah Huckabee Sanders",
        "Amended proclamation calling a special election for State Senator for District 18",
    ),
    "MS-qualifying": (
        "https://sos.ms.gov/content/CandidateQualifying/default.aspx",
        "official_body",
        "Mississippi Secretary of State",
        "Candidate Qualifying List",
    ),
    "LA-344": (
        f"{LA_CANDIDATES}#344:70161",
        "official_body",
        "Louisiana Secretary of State",
        "Candidate Inquiry, November 3, 2026: State Representative, 92nd Representative District",
    ),
    "LA-345": (
        f"{LA_CANDIDATES}#345:70170",
        "official_body",
        "Louisiana Secretary of State",
        "Candidate Inquiry, December 12, 2026: State Senator, 14th Senatorial District",
    ),
}
ELECTIONS = [
    ("2026-11-17:GU:runoff", "runoff", "2026-11-03:GU", "2026 Guam runoff election", "guam"),
    ("2026-12-01:GA:runoff", "runoff", "2026-11-03:GA", "2026 Georgia runoff election", "GA"),
    ("2026-12-01:MS:runoff", "runoff", "2026-11-03:MS", "2026 Mississippi runoff election", "MS"),
    ("2026-12-01:AR:runoff", "runoff", "2026-11-03:AR", "2026 Arkansas runoff election", "AR"),
    ("2026-12-12:LA:runoff", "runoff", "2026-11-03:LA", "2026 Louisiana runoff election", "LA"),
    ("2026-12-12:LA:special", "special", None, "December 12, 2026 Louisiana special election", "LA"),
    ("2026-12-15:AR:special", "special", None, "December 15, 2026 Arkansas special election", "AR-18"),
    ("2026-12-22:MS:special", "special", None, "December 22, 2026 Mississippi special election", "MS-34"),
    ("2027-01-09:LA:special", "special", None, "January 9, 2027 Louisiana special election", "LA-2027"),
    ("2027-01-19:MS:runoff", "runoff", None, "January 19, 2027 Mississippi runoff election", "MS-34"),
]
CONTESTS = {
    "2026-11-03:MS:house:70:gs": ("2026-11-03:MS", "two_round", "MS-70-77", "MS-qualifying"),
    "2026-11-03:MS:house:77:gs": ("2026-11-03:MS", "two_round", "MS-70-77", "MS-qualifying"),
    "2026-11-03:LA:house:92:gs": ("2026-11-03:LA", "two_round", "LA-92", "LA-344"),
    "2026-12-12:LA:senate:14:s": ("2026-12-12:LA:special", "two_round", "LA", "LA-345"),
    "2026-12-15:AR:senate:18:s": ("2026-12-15:AR:special", "fptp", "AR-18", None),
    "2026-12-22:MS:senate:34:s": ("2026-12-22:MS:special", "two_round", "MS-34", "MS-qualifying"),
}
STATES = {"MS": "Mississippi", "LA": "Louisiana", "AR": "Arkansas"}
PARTIES = {"Democrat": "democratic-party", "Republican": "republican-party"}


def fetch() -> None:
    for name, (url, *_) in SOURCES.items():
        try:
            if name.startswith("LA-3"):
                election, office = url.split("#")[1].split(":")
                try:
                    raw.cached(SOURCE, url)
                except LookupError:
                    response = httpx.post(
                        LA_CANDIDATES, json={"electionId": election, "officeIds": [office]}, timeout=60
                    )
                    response.raise_for_status()
                    raw.store(
                        SOURCE,
                        url,
                        response.content,
                        suffix=".html",
                        retrieved_at=datetime.now(UTC),
                        media_type="text/html",
                    )
            else:
                raw.fetch(SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".html")
        except httpx.HTTPError as error:
            print(f"{url}: {error}")


def parse() -> None:
    def tokens(key: str) -> list[str]:
        page = raw.cached(SOURCE, SOURCES[key][0]).path.read_text()
        return [html.unescape(token).strip() for token in re.split(r"<[^>]+>", page) if token.strip()]

    rows = []
    listed = tokens("MS-qualifying")
    for header, contest, width in (
        (
            "Candidates for State House of Representatives District 70 & 77 - Special Election",
            "2026-11-03:MS:house:{}:gs",
            3,
        ),
        ("Candidates for State Senate 34", "2026-12-22:MS:senate:{}:s", 2),
    ):
        start = listed.index(header)
        start = listed.index("District", start) + 1
        while start + width - 1 < len(listed) and listed[start + width - 1].isdigit():
            rows.append({"contest": contest.format(listed[start + width - 1]), "name": listed[start], "party": None})
            start += width
    for source, contest in (("LA-344", "2026-11-03:LA:house:92:gs"), ("LA-345", "2026-12-12:LA:senate:14:s")):
        listed = tokens(source)
        for index, token in enumerate(listed):
            if token == "Name / Address / Phone":
                rows.append(
                    {"contest": contest, "name": listed[index + 1], "party": listed[listed.index("Party", index) + 1]}
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema={"contest": pl.String, "name": pl.String, "party": pl.String})
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.group_by("contest").agg(pl.col("name")).sort("contest"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2026 elections after November 3 as scheduled: the Guam, Georgia, Mississippi, Arkansas, and Louisiana runoff dates, the Louisiana, Arkansas, and Mississippi legislative special elections through January 2027 with their candidates, and the November 3 Mississippi House 70 and 77 and Louisiana House 92 specials that can lead to December runoffs",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {}
        for key, (url, kind, publisher, title) in SOURCES.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                cs.add(
                    "source",
                    id=sources[key],
                    kind=kind,
                    publisher=publisher,
                    title=title,
                    url=url.split("#")[0],
                    license=None,
                )
            try:
                cs.artifact(raw.cached(SOURCE, url), sources[key])
            except LookupError:
                print(f"not fetched: {url}")

        elections = {}
        for key, kind, parent, title, source in ELECTIONS:
            day, state, _ = key.split(":")
            elections[key] = cs.key("us.election", key)
            cs.add(
                "election",
                id=elections[key],
                polity_id=cs.polity(f"US-{state}"),
                parent_id=cs.key("us.election", parent) if parent else None,
                kind=kind,
                date_from=date.fromisoformat(day),
                date_to=date.fromisoformat(day),
                status="scheduled",
            )
            name(elections[key], title)
            cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=elections[key])
            cs.add("citation", entity_id=elections[key], source_id=sources[source], locator=None)
        for key in ("2026-11-03:MS", "2026-11-03:LA"):
            elections[key] = cs.key("us.election", key)

        counts = {"contests": 0, "candidates": 0, "matched": 0}
        for key, (election, system, called, listed) in CONTESTS.items():
            day, state, chamber, district, _ = key.split(":")
            body, area, office = conn.execute(
                """select co.body_id, co.district_area_id, co.office_id from external_id x
                   join contest co on co.id = x.entity_id join election e on e.id = co.election_id
                   join entity en on en.id = co.id and en.retired_by is null
                   where x.scheme = 'us.contest' and x.value ~ %s and e.date_from < %s
                   order by e.date_from desc limit 1""",
                [rf"^[0-9-]+:{state}:{chamber}:{district}:[a-z]+$", date.fromisoformat(day)],
            ).fetchone()
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[election],
                kind="body_seats",
                office_id=office,
                body_id=body,
                district_area_id=area,
                seat_id=None,
                boundary_set_id=None,
                magnitude=1,
                term="remainder",
                round_number=1,
                previous_round_id=None,
                electoral_system_id=cs.key("us.electoral_system", system),
                default_result_kind="counted",
            )
            house = "House of Representatives" if chamber == "house" else "Senate"
            name(contest, f"2026 {STATES[state]} {house} special election, District {district}")
            kind = "general" if election.count(":") == 1 else election.rsplit(":", 1)[1]
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{day}-{kind}/state-{chamber}-{district}-special-election",
                entity_id=contest,
            )
            cs.add("citation", entity_id=contest, source_id=sources[called], locator=None)
            counts["contests"] += 1
            known = conn.execute(
                """select distinct ca.person_id, coalesce(ca.ballot_name, o.ballot_label, pn.text)
                   from contest co join contest_option o on o.contest_id = co.id
                   join candidacy ca on ca.option_id = o.id join entity e on e.id = ca.id and e.retired_by is null
                   left join name pn on pn.entity_id = ca.person_id and pn.is_preferred and pn.lang = 'en'
                   where co.district_area_id = %s""",
                [area],
            ).fetchall()
            for row in frame.filter(pl.col("contest") == key).iter_rows(named=True):
                tokens = [
                    token
                    for token in slug(re.sub(r"[\"'][^\"']*[\"']|\([^)]*\)", " ", row["name"])).split("-")
                    if token not in ("jr", "sr", "ii", "iii", "iv")
                ]
                pool = {
                    person
                    for person, held_text in known
                    if held_text
                    and (
                        held := [
                            token
                            for token in slug(held_text).split("-")
                            if token not in ("jr", "sr", "ii", "iii", "iv")
                        ]
                    )
                    and held[-1] == tokens[-1]
                    and held[0] == tokens[0]
                }
                if len(pool) == 1:
                    person = next(iter(pool))
                    counts["matched"] += 1
                else:
                    person = cs.key(f"{SOURCE}.person", f"{key}:{slug(row['name'])}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, row["name"])
                party = PARTIES.get(row["party"] or "")
                option = cs.key("us.option", f"{key}:{slug(row['name'])}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_label=row["name"],
                )
                candidacy = cs.key("us.candidacy", f"{key}:{slug(row['name'])}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    is_independent=row["party"] == "No Party",
                )
                cs.add("citation", entity_id=candidacy, source_id=sources[listed], locator=None)
                counts["candidates"] += 1
        print(counts)
        return cs.write()
