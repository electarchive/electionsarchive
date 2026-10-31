import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import govmint
from ea.store import DATA, connect

SOURCE = "us.ne_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://sos.nebraska.gov/sites/default/files/doc/elections/2026/Final_Statewide_General_Candidate_Filing_List_9.11.26.pdf"
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Final Statewide General Candidate List, November 3, 2026 General Election"
BODY = re.compile(
    r"^(.*?(?:Natural Resources District|Community College|Public Power District|Public Power and Irrigation District|"
    r"Reclamation District|Educational Service Unit No\. \d+|Metropolitan Utilities District of Omaha|"
    r"Learning Community of Douglas & Sarpy))"
)
KEEP = re.compile(
    r"Natural Resources|Community College|Public Power|Reclamation District|Educational Service Unit|"
    r"Metropolitan Utilities|Learning Community"
)
SEAT = re.compile(
    r"(Subdistrict \d+|Subcouncil \d+|Subdivision \d+|At Large|District \d\d|"
    r"(?:Keya Paha|Gosper County|Kearney County|Lincoln County|Phelps County|Cherry|Rock|West|Buffalo|Dawson|"
    r"Lincoln|North|East|South) Subdivision)"
)
NOISE = re.compile(r"\bFor Board(?: of)?\b|\bBoard of\b|\b(?:Directors|Governors|Regents)\b|\bFor\b|\bSubdivision\b|\bPage\b|\bCounties\b")
STATUS = re.compile(r"\b(Nonincumbent|Incumbent)\b")
SPLIT = (
    ("Central Nebraska Public Power and", "Central Nebraska Public Power and Irrigation District"),
    ("KBR Rural Public Power District", "KBR Rural Public Power District"),
    ("Wheat Belt Public Power District", "Wheat Belt Public Power District"),
)
ROLES = (
    ("Community College", "Governor", "college"),
    ("Educational Service Unit", "Board Member", "school"),
    ("Learning Community", "Subcouncil Member", "school"),
    ("Natural Resources District", "Director", "natural-resources"),
)


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        content = subprocess.run(
            ["curl", "-sSL", "-m", "180", "-A", "Mozilla/5.0", URL], capture_output=True, check=True
        ).stdout
        raw.store(
            SOURCE, URL, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )


def layout(path: Path) -> list[dict[str, str | int | bool]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True
    ).stdout
    entries: list[dict[str, str | int | list[str]]] = []
    current: dict[str, str | int | list[str]] | None = None
    for line in text.split("\n"):
        stripped = line.strip().lstrip("\f").strip()
        if not stripped or stripped.startswith(("Nebraska Secretary", "Office", "(if applicable)", "November 3")):
            continue
        fields = re.split(r"\s{2,}", stripped)
        term = next((i for i in range(len(fields) - 1) if fields[i].isdigit() and fields[i + 1].isdigit()), None)
        status = next((i for i, field in enumerate(fields) if STATUS.search(field)), None)
        if term is not None and status is not None and status >= term + 3:
            found = re.match(r"^(.*?)\s*\b(Nonincumbent|Incumbent)\b", fields[status])
            assert found is not None
            name_at = status - 1 if found.group(1) else status - 2
            current = {
                "pre": fields[:term],
                "term": int(fields[term]),
                "vote": int(fields[term + 1]),
                "name": fields[name_at],
                "incumbent": found.group(2) == "Incumbent",
                "cont": [],
            }
            entries.append(current)
        elif (
            current is not None
            and term is None
            and status is None
            and len(fields) <= 3
            and "@" not in stripped
            and "(" not in stripped
            and not stripped[0].isdigit()
        ):
            current["cont"].append(stripped)  # ty: ignore[possibly-missing-attribute]
    rows: list[dict[str, str | int | bool]] = []
    for entry in entries:
        pieces = [*entry["pre"], *(c for c in entry["cont"] if "Page " not in c and not re.search(r"NE \d{5}", c))]  # ty: ignore[not-iterable]
        full = " ".join(pieces)
        if not KEEP.search(full):
            continue
        seats = [m.group(1) for m in SEAT.finditer(full)]
        seat = seats[-1] if seats else ""
        body = full
        for part in seats:
            body = body.replace(part, " ", 1)
        body = re.sub(r"\s+of\s*$", "", " ".join(NOISE.sub(" ", body).split()))
        for lead, canon in SPLIT:
            if body.startswith(lead):
                tail = body[len(lead) :].replace("Irrigation District", "").replace(" County", "").strip()
                body = canon
                if lead.startswith("Central"):
                    seat = f"{tail} County Subdivision"
                elif tail:
                    seat = f"{tail} Subdivision"
        body = body.replace("Learning Community of Douglas & Sarpy", "Learning Community of Douglas and Sarpy Counties")
        rows.append(
            {
                "district": body,
                "seat": seat,
                "term": entry["term"],
                "vote": entry["vote"],
                "name": " ".join(str(entry["name"]).split()),
                "incumbent": entry["incumbent"],
            }
        )
    return rows


def parse() -> None:
    rows = layout(raw.cached(SOURCE, URL).path)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["district", "seat", "term", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def role(district: str) -> tuple[str, str | None]:
    for key, word, function in ROLES:
        if key in district:
            return word, function
    return "Director", None


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    full_term = {district: max(group["term"]) for (district,), group in frame.group_by(["district"])}
    with connect() as conn:
        state = conn.execute(
            """select t.polity_id, t.area_id, 'us/ne', c.code from polity_territory t
               join slug s on s.entity_id = t.polity_id and s.path = 'us/ne'
               join area_code c on c.area_id = t.area_id limit 1"""
        ).fetchone()
        assert state is not None
        existing: dict[str, tuple[UUID, UUID, str, str]] = {}
        for polity, area, text, path, code in conn.execute(
            """select p.id, t.area_id, n.text, s.path, c.code from polity p
               join polity_territory t on t.polity_id = p.id
               join area_code c on c.area_id = t.area_id
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where p.kind = 'special_district' and s.path like 'us/ne/%%'"""
        ).fetchall():
            existing[govmint.fold(text)] = (polity, area, path, code)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/ne/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska natural resources district, public power district, community college, educational service unit, and other district board races on the November 3, 2026 ballot, with candidates from the Secretary of State's final statewide list",
            polity="US-NE",
        )
        system = cs.key("us.electoral_system", "fptp")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:NE")
        made: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        elections: set[UUID] = set()
        kept = 0
        for (district, seat, term), people in frame.group_by(["district", "seat", "term"], maintain_order=True):
            word, function = role(district)
            if district not in made:
                twin = existing.get(govmint.fold(district))
                if twin is not None:
                    made[district] = (twin[0], twin[1], district, twin[2], twin[3])
                else:
                    special = govmint.special(conn, cs, "NE", state, district, slugs, function)
                    if special is None:
                        print("no district:", district)
                        continue
                    made[district] = special
            polity, area, place, path, code = made[district]
            election = cs.key("us.election", f"{DAY}:NE:{code}")
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
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            remainder = term < full_term[district]
            label = f"{seat} (unexpired term)" if remainder else seat
            title = f"{place} {word} {label}"
            token = f"{slug(word)}-{slug(seat)}" + ("-unexpired" if remainder else "")
            owner = cs.key("us.office", f"NE:{code}:{token}")
            contest_key = f"{DAY}:NE:{code}:{token}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{token}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            if owner in cs.minted:
                cs.add("office", id=owner, polity_id=polity, kind="other")
                cs.add("name", entity_id=owner, text=title, lang="en", kind="official", is_preferred=True)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=owner,
                district_area_id=area,
                magnitude=int(people.row(0, named=True)["vote"]),
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
                term="remainder" if remainder else "full",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {title} election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source, locator=None)
            for person_row in people.iter_rows(named=True):
                ballot = person_row["name"]
                who = slug(ballot)
                person = cs.key(f"{SOURCE}.person", f"{contest_key}:{who}")
                option = cs.key("us.option", f"{contest_key}:{who}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=ballot, lang="en", kind="official", is_preferred=True)
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                candidacy = cs.key("us.candidacy", f"{contest_key}:{who}:head")
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
