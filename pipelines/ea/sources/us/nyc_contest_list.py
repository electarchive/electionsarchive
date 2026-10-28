import re
import subprocess
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_contest_list"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Board of Elections in the City of New York"
DAY = "2026-11-03"
URL = "https://www.vote.nyc/sites/default/files/pdf/candidates/2026/9_11_2026%2010_26_11%20AM_General%20Contest%20List%20Web%20Version.pdf"
TITLE = "General Election 2026 Contest List (tentative, printed September 11, 2026)"
ELECTION = f"{DAY}:NY:3651000"
ELECTION_PATH = "us/ny/new-york-city/2026-11-03-general"
COUNTIES = {"New York": "36061", "Bronx": "36005", "Kings": "36047", "Queens": "36081", "Richmond": "36085"}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Conservative": "NY:conservative-party",
    "Working Families": "NY:working-families-party",
}
HEADER = re.compile(r"^\s*(.+) - (.+?), # to be elected: (\d+)\s*$")
SECTION = re.compile(r"\s(New York|Bronx|Kings|Queens|Richmond) All Parties and Independent Bodies")
JUDICIAL = {
    "Surrogate": "surrogate",
    "Justice of the Supreme Court": "supreme-court",
    "Judge of the Civil Court - County": "civil-court",
    "Judge of the Civil Court - District": "civil-court-district",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    borough = office = area = None
    magnitude = 0
    body: list[str] = []
    for line in text.splitlines():
        if (section := SECTION.search(line)) is not None:
            borough = section[1]
            continue
        if (header := HEADER.match(line)) is not None:
            office, area, magnitude, body = header[1], header[2], int(header[3]), []
            if office.startswith("Judge of the Civil Court - District - "):
                office, area = "Judge of the Civil Court - District", office.split(" - ", 2)[2]
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if office is None or not line.strip() or line.strip().startswith(("Page ", "BOARD OF", "IN THE CITY", "PRINTED", "Party\\Ind")):
            continue
        if office.startswith("Proposal Number"):
            if parts[0] in ("Yes", "No"):
                rows.append((borough, office, area, magnitude, parts[0], " ".join(body), ""))
            else:
                body.append(line.strip())
        elif len(parts) >= 3:
            rows.append((borough, office, area, magnitude, parts[0], parts[1], parts[2]))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema=["borough", "office", "area", "magnitude", "line", "name", "address"], orient="row"
    ).write_parquet(PARSED / "rows.parquet")


def emit():
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's November 3, 2026 judicial contests, charter proposals, and candidates from the Board of Elections' contest list",
            polity="US",
        )
        city = cs.key("us.polity", "new-york-city")
        election = cs.key("us.election", ELECTION)
        place = cs.area("us.place", "3651000")
        if cs.minted & {city, election, place}:
            raise ValueError("New York City and its 2026 general election must already exist")
        fptp = cs.key("us.electoral_system", "fptp")
        block = cs.key("us.electoral_system", "block")
        referendum = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record")
        cs.artifact(raw.cached(SOURCE, URL), source)

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        parties: dict[str, UUID | None] = {}
        added: set[UUID] = set()
        for line in frame.filter(~pl.col("office").str.starts_with("Proposal"))["line"].unique().sort():
            key = PARTIES.get(line, f"NY:{slug(line)}")
            party = cs.key("us.party", key)
            if party in cs.minted:
                cs.add("party", id=party, kind="party", polity_id=cs.polity("US-NY"))
                name(party, line)
            parties[line] = party

        for office_text, area_text, borough, magnitude in (
            frame.filter(pl.col("office").is_in(list(JUDICIAL)) | pl.col("office").str.starts_with("Proposal"))
            .select("office", "area", "borough", "magnitude")
            .unique(subset=["office", "area", "borough"])
            .sort("borough", "office", "area")
            .iter_rows()
        ):
            question = office_text.startswith("Proposal")
            if question and borough != "New York":
                continue
            rows = frame.filter(pl.col("office") == office_text, pl.col("area") == area_text, pl.col("borough") == borough)
            if question:
                heading = re.sub(r"^Proposal Number \d+, a Question: ", "", office_text)
                heading = heading.removesuffix(" - Citywide").strip()
                seat = f"proposal-{slug(heading)[:48].rstrip('-')}"
                title = heading
                area = place
                office = None
            else:
                fips = COUNTIES[borough]
                county = borough
                area = cs.area("us.county", fips)
                number = re.match(r"(\d+)", area_text)
                number = number[1] if number else ""
                kind = JUDICIAL[office_text]
                if kind == "surrogate":
                    seat, title, office_key = f"surrogate-{slug(county)}", f"{county} County Surrogate", f"NY:{fips}:surrogate"
                elif kind == "supreme-court":
                    ordinal = area_text.split(" Judicial")[0]
                    seat, title = f"supreme-court-{number}-{slug(county)}", f"{ordinal} Judicial District Supreme Court Justice"
                    office_key = f"NY:{fips}:supreme-court:{number}"
                elif kind == "civil-court":
                    seat, title = f"civil-court-{slug(county)}", f"{county} County Civil Court Judge"
                    office_key = f"NY:{fips}:civil-court"
                else:
                    ordinal = area_text.split(" Municipal")[0]
                    seat = f"civil-court-district-{number}-{slug(county)}"
                    title = f"{ordinal} Municipal Court District, {county} County Civil Court Judge"
                    office_key = f"NY:{fips}:civil-court-district-{number}"
                    area = None
                office = cs.key("us.office", office_key)
                if office in cs.minted:
                    raise ValueError(f"{title}: the office must already exist")
            contest_key = f"{DAY}:new-york-city:{seat}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question" if question else "office",
                office_id=office,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=referendum if question else fptp if magnitude == 1 else block,
                default_result_kind="counted",
                term=None if question else "full",
            )
            name(
                contest,
                f"2026 New York City ballot question: {title}" if question else f"2026 {title} election",
            )
            cs.add("slug", path=f"{ELECTION_PATH}/{seat}", entity_id=contest)
            if question:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                for position, answer in enumerate(("Yes", "No"), start=1):
                    cs.add(
                        "contest_option",
                        id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=answer,
                    )
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1ftext\x1fen\x1f{source}\x1f{URL}"),
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=f"{heading}: {rows['name'][0]}",
                    url=URL,
                    source_id=source,
                    locator={"heading": office_text},
                )
                continue
            peers: dict[tuple[str, str], UUID | None] = {}
            for ballot_name, person_id in conn.execute(
                """select ca.ballot_name, ca.person_id from candidacy ca
                   join contest_option o on o.id = ca.option_id join contest c on c.id = o.contest_id
                   where c.office_id = %s and ca.role = 'head' and ca.ballot_name <> ''""",
                [office],
            ).fetchall():
                words = re.sub(r"[^a-z ]", "", ballot_name.lower()).split()
                token = (words[0], words[-1])
                peers[token] = person_id if peers.setdefault(token, person_id) == person_id else None
            rows = rows.with_columns(slug=pl.col("name").map_elements(slug, return_dtype=pl.String))
            for candidate in rows.unique("slug", keep="first", maintain_order=True)["name"]:
                lines = rows.filter(pl.col("slug") == slug(candidate))["line"].to_list()
                root_key = f"{contest_key}:{slug(candidate)}"
                option = cs.key("us.option", root_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_label=candidate,
                )
                words = re.sub(r"[^a-z ]", "", candidate.lower()).split()
                person = peers.get((words[0], words[-1]))
                if person is None:
                    person = cs.key("us.nyc_general.person", root_key)
                    if person in cs.minted and person not in added:
                        added.add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                head = cs.key("us.candidacy", f"{root_key}:head")
                independent = all(slug(line) == slug(candidate) for line in lines)
                cs.add(
                    "candidacy",
                    id=head,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=independent,
                )
                for line in lines:
                    if slug(line) != slug(candidate):
                        cs.add("candidacy_nomination", candidacy_id=head, party_id=parties[line], kind="nominated")
        return cs.write()
