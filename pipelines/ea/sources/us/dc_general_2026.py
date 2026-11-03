import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.dc_general_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
PUBLISHER = "District of Columbia Board of Elections"
TITLE = "Candidates and sample ballots for the November 3, 2026 general election"
PAGE = "https://www.dcboe.org/Elections/2026-Elections"
ASSET = "https://www.dcboe.org/getContentAsset/"
CANDIDATES = (
    ASSET
    + "1029b813-fb27-4055-9218-fbe40360aacb/5ebbd046-860b-4ee1-81c8-43d0e3bc446e/2026-General-CANDIDATES-07152026.pdf?language=en"
)
ANC = (
    ASSET
    + "5532a17e-37dc-4d52-a54b-a6ce21fdbe4b/5ebbd046-860b-4ee1-81c8-43d0e3bc446e/2026-General-CANDIDATES-07162026.pdf?language=en"
)
BALLOT = ASSET + "bf78fba0-50f2-40c4-ad32-0f78255d725a/5ebbd046-860b-4ee1-81c8-43d0e3bc446e/Ward1C.pdf?language=en"
PLACE = "1150000"
HEADERS = {
    "Chairman of the Council of the District of Columbia": "chair",
    "At-Large Member of the Council": "at-large",
    "Shadow United States Senator": "shadow-united-states-senator",
    "United States Senator": "shadow-united-states-senator",
    "United States Representative": "shadow-united-states-representative",
}
PARTIES = {
    "Democrat": "democratic-party",
    "Republican": "republican-party",
    "Statehood Green": "DC:d-c-statehood-green-party",
}
WARD = re.compile(r"Ward (\d) Member of the (Council|State Board of Education)$")
CANDIDATE = re.compile(
    r"^(\S.*?)(?:\s+-\s+(Democrat|Republican|Statehood Green|Independent|Libertarian))?(?:\s{2,}.*)?$"
)
ANC_ROW = re.compile(r"^\s*([1-8][A-G]\d\d)(?:\s{2,}(\S.*?))?(?:\s{2,}|$)")
INITIATIVE = ("86", "Prohibiting Force-Feeding of Birds Act of 2026")


def text(url: str, layout: bool) -> str:
    path = raw.cached(SOURCE, url).path
    args = ["pdftotext", *(["-layout"] if layout else []), str(path), "-"]
    return subprocess.run(args, capture_output=True, check=True, text=True).stdout


def fetch() -> None:
    for url in (CANDIDATES, ANC, BALLOT):
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows: list[tuple[str, str, str]] = []
    section = None
    for line in text(CANDIDATES, True).splitlines()[2:]:
        head = line.strip()
        if not head or head.startswith("Write-In"):
            continue
        ward = WARD.fullmatch(head)
        if ward:
            section = f"{'council' if ward[2] == 'Council' else 'sboe'}:ward-{ward[1]}"
        elif head in HEADERS:
            section = HEADERS[head]
        elif head in (
            "Delegate to the House of Representatives",
            "Mayor of the District of Columbia",
            "Attorney General",
        ):
            section = None
        elif section is not None and not line.startswith(" "):
            found = CANDIDATE.fullmatch(line)
            if found:
                rows.append((section, found[1].strip(), found[2] or ""))
    for line in text(ANC, True).splitlines():
        found = ANC_ROW.match(line)
        if found:
            rows.append((f"anc:{found[1].lower()}", (found[2] or "").strip(), ""))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["section", "candidate", "party"], orient="row").write_parquet(
        PARSED / "candidates.parquet"
    )
    print(len(rows), "rows")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    ballot = text(BALLOT, False)
    wording = re.search(
        r"INITIATIVE MEASURE NO\. 86\s+Prohibiting Force-Feeding of Birds\s+Act of 2026\.\s+If enacted, this Initiative would:",
        ballot,
    )
    if wording is None:
        raise ValueError("no Initiative 86 on the sample ballot")
    items = re.findall(
        r"\(([a-e])\) ((?:(?!\n\n\(|\n\(|\nVote for).)+?)(?=\n\n|\n\(|$)", ballot[wording.end() :], re.DOTALL
    )
    summary = "If enacted, this Initiative would: " + " ".join(
        f"({letter}) {re.sub(r'\s+', ' ', body).strip()}" for letter, body in items[:5]
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The District of Columbia's November 3, 2026 council, State Board of Education, shadow delegation, "
            "Advisory Neighborhood Commission, and Initiative 86 contests from the Board of Elections' candidate lists and sample ballot",
            polity="US-DC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        for url in (CANDIDATES, ANC, BALLOT):
            cs.artifact(raw.cached(SOURCE, url), source)
        election = cs.key("us.election", f"{DAY}:DC")
        if election in cs.minted:
            raise ValueError("no 2026-11-03 DC election")
        taken = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme in ('us.contest', 'us.office', 'us.local_district')"
            )
        }
        ((state_area,),) = conn.execute(
            "select area_id from reporting_unit where scheme_code = 'us.state' and code = '11'"
        ).fetchall()
        city = cs.area("us.place", PLACE)
        council = cs.key("us.body", f"DC:{PLACE}:council")
        ((polity,),) = conn.execute("select polity_id from body where id = %s", [council]).fetchall()
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "referendum")}

        def name(entity: UUID, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        def office(label: str, key: str) -> UUID:
            found = cs.key("us.office", key)
            if key not in taken:
                taken.add(key)
                cs.add("office", id=found, polity_id=cs.polity("US-DC"), kind="other")
                name(found, f"{label} of the District of Columbia")
            return found

        def district(code: str, label: str, parent: UUID) -> UUID:
            area = cs.area("us.local_district", code)
            if code not in taken:
                taken.add(code)
                name(area, label)
                cs.add("area", id=area, scheme_code="us.local_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=parent, valid_from=None)
                cs.add(
                    "reporting_unit",
                    id=cs.unit("us.local_district", code),
                    polity_id=polity,
                    scheme_code="us.local_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
            return area

        persons: set[str] = set()
        count = 0
        for (section,), group in frame.group_by("section", maintain_order=True):
            kind, _, tail = section.partition(":")
            if kind == "council":
                label = f"Ward {tail.removeprefix('ward-')}"
                contest_key = f"{DAY}:DC:{PLACE}:council:{tail}"
                area = district(f"DC:{PLACE}:council:{tail}", f"{label}, District of Columbia", city)
                title = f"{DAY.year} Council of the District of Columbia election, {label}"
                path = f"us/dc/{DAY}-general/council-{tail}"
            elif section in ("chair", "at-large"):
                label = "Chairman" if section == "chair" else "At-Large"
                contest_key = f"{DAY}:DC:{PLACE}:council:{section}"
                area = city
                title = f"{DAY.year} Council of the District of Columbia election, {label}"
                path = f"us/dc/{DAY}-general/council-{section}"
            elif kind == "sboe":
                label = f"Ward {tail.removeprefix('ward-')}"
                contest_key = f"{DAY}:DC:{PLACE}:state-board-of-education:{tail}"
                area = district(f"DC:{PLACE}:council:{tail}", f"{label}, District of Columbia", city)
                title = f"{DAY.year} District of Columbia Member of the State Board of Education election ({label})"
                path = f"us/dc/{DAY}-general/state-board-of-education-{tail}"
            elif kind == "anc":
                smd = tail.upper()
                contest_key = f"{DAY}:DC:{PLACE}:anc:{tail}"
                area = district(
                    f"DC:{PLACE}:anc:{tail}", f"Advisory Neighborhood Commission {smd}, District of Columbia", city
                )
                title = f"{DAY.year} Advisory Neighborhood Commission {smd} election"
                path = f"us/dc/{DAY}-general/anc-{tail}"
            else:
                label = (
                    "Shadow United States Senator" if "senator" in section else "Shadow United States Representative"
                )
                contest_key = f"{DAY}:DC:{section}"
                area = state_area
                title = f"{DAY.year} District of Columbia {label} election"
                path = f"us/dc/{DAY}-general/{section}"
            if contest_key in taken:
                raise ValueError(f"{contest_key} is taken")
            contest = cs.key("us.contest", contest_key)
            body_seats = kind == "council" or section in ("chair", "at-large")
            if kind == "council" or section in ("chair", "at-large"):
                office_id = None
            elif kind == "sboe":
                office_id = office(
                    "Member of the State Board of Education", "member-of-the-state-board-of-education:DC"
                )
            elif kind == "anc":
                office_id = office("Advisory Neighborhood Commissioner", "advisory-neighborhood-commissioner:DC")
            else:
                office_id = office(label, f"{section}:DC")
            magnitude = 2 if section == "at-large" else 1
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if body_seats else "office",
                office_id=office_id,
                body_id=council if body_seats else None,
                district_area_id=area,
                term="full",
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="counted",
            )
            name(contest, title)
            cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            count += 1
            for position, row in enumerate(group.filter(pl.col("candidate") != "").iter_rows(named=True)):
                option = cs.key("us.option", f"{contest_key}:{slug(row['candidate'])}")
                party = PARTIES.get(row["party"])
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position + 1,
                    ballot_label=row["candidate"],
                )
                person = cs.key("us.dc_boe.person", f"DC:{slug(row['candidate'])}")
                if person in cs.minted and slug(row["candidate"]) not in persons:
                    persons.add(slug(row["candidate"]))
                    cs.add("person", id=person)
                    name(person, row["candidate"])
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(row['candidate'])}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["candidate"],
                    is_independent=row["party"] == "Independent",
                )
            cs.add(
                "contest_option",
                id=cs.key("us.option", f"{contest_key}:write-ins"),
                contest_id=contest,
                kind="write_in",
                ballot_label="Write-ins",
            )
        number, act = INITIATIVE
        contest_key = f"{DAY}:DC:question:initiative-{number}"
        if contest_key in taken:
            raise ValueError(f"{contest_key} is taken")
        contest = cs.key("us.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=state_area,
            electoral_system_id=systems["referendum"],
            round_number=1,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="initiative")
        name(contest, f"{DAY.year} District of Columbia Initiative {number}: {act.removesuffix(' Act of 2026')}")
        cs.add("slug", path=f"us/dc/{DAY}-general/initiative-{number}", entity_id=contest)
        cs.add("citation", entity_id=contest, source_id=source)
        for position, answer in enumerate(("Yes", "No")):
            cs.add(
                "contest_option",
                id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                contest_id=contest,
                kind="answer",
                ballot_position=position + 1,
                ballot_label=answer,
            )
        cs.add(
            "ballot_question_text",
            contest_id=contest,
            kind="text",
            lang="en",
            body=f"Initiative Measure No. {number}, {act}. {summary}",
            url=BALLOT,
            source_id=source,
        )
        print(count + 1, "contests")
        return cs.write()
