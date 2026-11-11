import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint
from ea.sources.us.sc_general_2026 import DAY, INBOX, LOCAL, PAGE, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.sc_local_2026"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
TITLE = "2026 Statewide General Election certified results file, solicitor, municipal, and school district contests"
PLACES = {
    "4574680": "Ware Shoals",
    "4550695": "North Augusta",
    "4514950": "Clemson",
    "4532245": "Hardeeville",
    "4535890": "Irmo",
}
SCHOOLS = {
    "4500810": "Anderson County School District 2",
    "4500900": "Anderson County School District 5",
    "4502310": "Greenville County School District",
    "4502760": "Lexington County School District 3",
    "4503510": "Spartanburg County School District 2",
}
CIRCUITS = {
    "SC:circuit-3": ("Third Judicial Circuit", ("45027", "45061", "45085", "45089")),
    "SC:circuit-5": ("Fifth Judicial Circuit", ("45055", "45079")),
    "SC:circuit-6": ("Sixth Judicial Circuit", ("45023", "45039", "45057")),
    "SC:circuit-12": ("Twelfth Judicial Circuit", ("45041", "45067")),
    "SC:circuit-13": ("Thirteenth Judicial Circuit", ("45045", "45077")),
    "SC:circuit-15": ("Fifteenth Judicial Circuit", ("45043", "45051")),
}
TITLES = {
    "4574680:town-council": ("Ware Shoals Town Council", "full"),
    "4574680:town-council-unexpired": ("Ware Shoals Town Council", "remainder"),
    "4550695:city-council-at-large": ("North Augusta City Council, At Large", "full"),
    "4514950:city-council": ("Clemson City Council", "full"),
    "4532245:city-council": ("Hardeeville City Council", "full"),
    "4500810:board-of-trustees-at-large": ("Anderson County School District 2 Board of Trustees, At Large", "full"),
    "45007:board-of-education-district-5": ("Anderson County Board of Education, District 5", "full"),
    "4502310:board-of-trustees-district-18": (
        "Greenville County School District Board of Trustees, District 18",
        "full",
    ),
    "4502310:board-of-trustees-district-28": (
        "Greenville County School District Board of Trustees, District 28",
        "full",
    ),
    "4502760:board-of-trustees": ("Lexington County School District 3 Board of Trustees", "full"),
    "4503510:measure:question-1": ("Referendum Question 1", "full"),
    "4503510:measure:question-2": ("Referendum Question 2", "full"),
    "4535890:measure:form-of-government": ("Form of Government Referendum", "full"),
}
CHEROKEE = (
    "Cherokee County Voter Registration and Elections",
    "2026 November Referendums Info",
    "https://cherokeecountysc.gov/wp-content/uploads/2026/08/2026-November-Referendums-Info.pdf",
)
IRMO = (
    "Town of Irmo",
    "Final public informational meeting ahead of the November ballot referendum on form of government",
    "https://www.swlexledger.com/single-post/town-of-irmo-to-hold-final-public-informational-meeting-ahead-of-november-ballot-referendum-on-form",
)
WORDING = {
    "4503510:measure:question-1": (
        *CHEROKEE,
        'Shall the Board of Trustees of School District No. 2 of Spartanburg County, South Carolina (the "School District"), be empowered to issue, at one time or from time to time, general obligation bonds of the School District, in a principal amount of not exceeding $156,600,000.00 the proceeds of which shall be used to finance the costs (including architectural, engineering, legal and related fees) of constructing and equipping a new K-8 school in the Boiling Springs community and constructing and equipping additional classroom space at Cooley Springs-Fingerville Elementary School and Mayo Elementary School?',
    ),
    "4503510:measure:question-2": (
        *CHEROKEE,
        'If and only if Question 1 above is approved, shall the Board of Trustees of School District No. 2 of Spartanburg County, South Carolina (the "School District") be empowered to issue, at one time or from time to time, general obligation bonds of the School District, in a principal amount of not exceeding $30,000,000, the proceeds of which shall be used to finance the costs (including architectural, engineering, legal and related fees) of stadium upgrades at both of the School District\'s high schools, athletic improvements at all middle schools, and improvements to playgrounds at all elementary schools?',
    ),
    "4535890:measure:form-of-government": (
        *IRMO,
        "Shall the Town of Irmo change its form of government from the Council form to the Council-Manager form?",
    ),
}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}


def fetch() -> None:
    raw.store(
        SOURCE,
        PAGE,
        INBOX.read_bytes(),
        suffix=".zip",
        retrieved_at=raw.cached("us.sc_general_2026", PAGE).retrieved_at,
        media_type="application/zip",
    )


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, PAGE).path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xml"))
    rows = []
    for contest in root.iter("Contest"):
        found = LOCAL.get(" ".join(re.findall(r"[a-z]+|\d+", (contest.get("text") or "").lower())))
        if found is None:
            continue
        for position, choice in enumerate(contest.findall("Choice")):
            if choice.get("text") == "Write-In":
                continue
            party = choice.get("party") or "NON"
            rows.append(
                {
                    "token": found[0],
                    "area": found[1],
                    "seats": int(contest.get("voteFor") or 1),
                    "position": position + 1,
                    "name": (choice.get("text") or "").removeprefix(f"{party} "),
                    "party": party,
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "choices.parquet")
    print(frame["token"].n_unique(), "contests,", frame.height, "choices")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "choices.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina circuit solicitor, town and city council, and school board races and local questions on the November 3, 2026 ballot, with the judicial circuits, towns, and school districts that hold them",
            polity="US-SC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.enr-scvotes.org/SC/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if census in cs.minted:
            raise LookupError("no Census cartographic boundary source")
        civil = civmint.Civil(conn, cs, "45", "SC")
        state_polity = cs.polity("US-SC")
        state_area = cs.area("us.state", "45")
        state_election = cs.key("us.election", f"{DAY}:SC")
        homes: dict[str, tuple[UUID, UUID, str, str]] = {}
        for geoid, text in PLACES.items():
            key = civil.mint("us.place", geoid, text)
            assert key is not None
            polity, area, place, path, _ = civil.found[key]
            homes[geoid] = (polity, area, place, path)
        for geoid, text in SCHOOLS.items():
            key = civil.mint("us.school_district", geoid, text)
            assert key is not None
            polity, area, place, path, _ = civil.found[key]
            homes[geoid] = (polity, area, place, path)
        polity, area, place, path, _ = civil.found["us.county:45007"]
        homes["45007"] = (polity, area, place, path)
        for code, (label, counties) in CIRCUITS.items():
            polity = cs.key("us.polity", f"judicial:{code}")
            area = cs.area("us.judicial_district", code)
            path = f"us/sc/{slug(label)}"
            homes[code] = (polity, area, label, path)
            if polity not in cs.minted:
                continue
            if civil.slugs.get(path, polity) != polity:
                raise ValueError(f"{label} would take {path}")
            civil.slugs[path] = polity
            cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
            cs.add("name", entity_id=polity, text=f"{label}, SC", lang="en", kind="official", is_preferred=True)
            cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
            cs.add("slug", path=path, entity_id=polity)
            cs.add("area", id=area, scheme_code="us.judicial_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=state_area)
            cs.add("name", entity_id=area, text=f"{label}, SC", lang="en", kind="official", is_preferred=True)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.judicial_district", code),
                polity_id=polity,
                scheme_code="us.judicial_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            geom, point, count = conn.execute(
                """with latest as (
                       select distinct on (ac.code) ac.code, g.geom from area_geometry g
                       join area a on a.id = g.area_id and a.scheme_code = 'us.county'
                       join area_code ac on ac.area_id = g.area_id
                       where ac.code = any(%s) order by ac.code, g.valid_from desc nulls last)
                   select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(geom)), 3))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_makevalid(st_union(geom)))), 'hex'), count(*) from latest""",
                [list(counties)],
            ).fetchone() or (None, None, 0)
            if count != len(counties):
                raise ValueError(f"{label}: {count} of {len(counties)} county shapes")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
        wording_sources: set[UUID] = set()
        elections: set[UUID] = set()
        offices: set[UUID] = set()
        referendum = cs.key("us.electoral_system", "referendum")
        fptp = cs.key("us.electoral_system", "fptp")
        for (token, code), choices in frame.group_by("token", "area", maintain_order=True):
            contest_key = f"{DAY}:SC:{token}"
            contest = cs.key("us.contest", contest_key)
            owner_code = token.split(":")[0]
            if token.startswith("solicitor:"):
                polity, area, label, _ = homes[code]
                election = state_election
                title, term = f"Solicitor, {label}", "full"
                contest_path = f"us/sc/{DAY}-general/solicitor-{slug(label)}"
            else:
                polity, _, place, path = homes[owner_code]
                area = homes[code][1]
                election = cs.key("us.election", f"{DAY}:SC:{owner_code}")
                if election in cs.minted and election not in elections:
                    elections.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polity,
                        parent_id=state_election,
                        kind="general",
                        date_from=DAY,
                        date_to=DAY,
                        date_precision="day",
                        status="held" if DAY <= date.today() else "scheduled",
                    )
                    cs.add(
                        "name",
                        entity_id=election,
                        text=f"{DAY.year} {place} general election",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
                title, term = TITLES[token]
                contest_path = f"{path}/{DAY}-general/{token.split(':', 1)[1].replace(':', '-')}"
            if civil.slugs.get(contest_path, contest) != contest:
                raise ValueError(f"{contest_key} would take {contest_path}")
            civil.slugs[contest_path] = contest
            if ":measure:" in token:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=area,
                    electoral_system_id=referendum,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="optional_referendum")
                publisher, wording_title, wording_url, wording = WORDING[token]
                wording_source = cs.source(publisher, wording_title)
                if wording_source not in wording_sources:
                    wording_sources.add(wording_source)
                    cs.add(
                        "source",
                        id=wording_source,
                        kind="official_body",
                        publisher=publisher,
                        title=wording_title,
                        url=wording_url,
                        license="Public record",
                    )
                cs.add(
                    "ballot_question_text",
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=wording,
                    url=wording_url,
                    source_id=wording_source,
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{DAY.year} {place}: {title}",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                for choice in choices.iter_rows(named=True):
                    cs.add(
                        "contest_option",
                        id=cs.key("us.option", f"{contest_key}:{slug(choice['name'].split(',')[0])}"),
                        contest_id=contest,
                        kind="answer",
                        ballot_position=choice["position"],
                        ballot_label=choice["name"],
                    )
            else:
                office = cs.key("us.office", f"SC:{owner_code}:{slug(title)}")
                if office in cs.minted and office not in offices:
                    offices.add(office)
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    cs.add("name", entity_id=office, text=title, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    magnitude=choices["seats"][0],
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                    term=term,
                )
                label = f"{title} (unexpired term)" if term == "remainder" else title
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{DAY.year} {label} election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                for choice in choices.iter_rows(named=True):
                    ballot = choice["name"]
                    who = slug(ballot)
                    person = cs.key(f"{SOURCE}.person", f"{contest_key}:{who}")
                    option = cs.key("us.option", f"{contest_key}:{who}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
                    party = PARTIES.get(choice["party"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party) if party else None,
                        ballot_position=choice["position"],
                        ballot_label=ballot,
                    )
                    candidacy = cs.key("us.candidacy", f"{contest_key}:{who}:head")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=False,
                    )
                    cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
        return cs.write()
