import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.navajo"
VERSION = "1"
PUBLISHER = "Navajo Election Administration"
SITE = "https://navajoelections.navajo-nsn.gov"
FOLDER = f"{SITE}/Portals/0/2022%20NN%20Candidate%20Ballets/2022%20NN%20ELECTION%20RESULTS"
BY_CHAPTER = f"{FOLDER}/2022NNResultsbyChapter.pdf"
PARTICIPATION = f"{FOLDER}/2022NNVoterParticipationbyChapter.pdf"
CERTIFIED = [
    f"{FOLDER}/Official%20Results/{name}.pdf"
    for name in (
        "NN%20President%20Official%20Certified%20Results%202022%20General%20Election",
        "Navajo%20Board%20of%20Election%20Supervisors%20Official%20Certified%20Results%202022%20General%20Election",
        "NN%20Board%20of%20Education%20Official%20Certified%20Results%202022%20General%20Election",
        "NN%20Council%20Delegate%20Official%20Certified%20Results%202022%20General%20Elections_all",
        "NN%20School%20Board%20Special%20%20Certified%202022%20General%20Election%20Results",
    )
]
PAGE = f"{SITE}/2022-NN-GENERAL-ELECTION-RESULTS"
DAY = date(2022, 11, 8)
POLITY = "tribal:navajo-nation"
NATION = "Navajo Nation"
SCHEME = "us.navajo_chapter"
CANDIDATE = re.compile(r"^(.*?\S)\s{2,}([\d,]+)\s+([\d.]+)%$")
BODIES = {
    "council": ("Navajo Nation Council", "unicameral", "navajo-nation-council"),
    "supervisors": ("Navajo Board of Election Supervisors", "council", "board-of-election-supervisors"),
    "education": ("Navajo Nation Board of Education", "council", "board-of-education"),
}
SCHOOL_SEAT = re.compile(r"^(.*?)\s+(I|II|III)$")
CERTIFIED_TOTALS = Path(__file__).with_name("navajo_2022_certified.tsv")
RECOUNTS = {
    "tiistsohsikaad": {
        "045": (143, 181),
        "046": (303, 351),
        "047": (431, 544),
        "103": (415, 222),
        "109": (153, 173),
        "110": (156, 185),
    },
    "littlewater": {
        "055": (130, 193),
        "057": (102, 160),
        "059": (360, 238),
        "060": (102, 201),
        "062": (287, 291),
        "064": (213, 108),
        "093": (192, 142),
        "102": (106, 123),
    },
}
WHOLE_BOARD = {
    "ALAMO NAVAJO SHOOL",
    "RAMAH NAVAJO SCHOOL BOARD",
    "TO'HAJIILEE COMMUNITY SCHOOL",
    "COMMISSIONER - NASCHITTI",
}
SCHOOL_NAMES = {"NAVAJO PREPARATORYSCHOOL": "NAVAJO PREPARATORY SCHOOL", "ALAMO NAVAJO SHOOL": "ALAMO NAVAJO SCHOOL"}

Contest = dict[str, object]


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", slug(text))


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in (BY_CHAPTER, PARTICIPATION, *CERTIFIED):
        raw.fetch(SOURCE, url, suffix=".pdf")


def text(path: Path, *crop: int) -> list[str]:
    box = [str(value) for pair in zip(("-x", "-y", "-W", "-H"), crop, strict=False) for value in pair]
    return subprocess.run(
        ["pdftotext", "-layout", *box, str(path), "-"], check=True, capture_output=True, text=True
    ).stdout.split("\f")


def chapters() -> list[tuple[str, str, int, int]]:
    rows = []
    for line in "\n".join(text(raw.cached(SOURCE, PARTICIPATION).path)).splitlines():
        found = re.match(r"^\s*(\d{3})\s+(.+?)\s{2,}(\d+)\s+(\d+)\s+[\d.]+%$", line)
        if found:
            rows.append((found[1], found[2].strip(), int(found[3]), int(found[4])))
    return rows


def blocks(column: str) -> list[tuple[str, list[tuple[str, int]]]]:
    out: list[tuple[str, list[tuple[str, int]]]] = []
    heading: list[str] = []
    after = False
    for line in column.splitlines():
        line = line.strip()
        if not line:
            after = False
            continue
        found = CANDIDATE.match(line)
        if found:
            if heading:
                out.append((" / ".join(heading), []))
                heading = []
            out[-1][1].append((found[1], int(found[2].replace(",", ""))))
            after = True
        elif after:
            name, votes = out[-1][1][-1]
            out[-1][1][-1] = (f"{name} {line}", votes)
        else:
            heading.append(line)
    return out


def parse() -> dict[str, Contest]:
    path = raw.cached(SOURCE, BY_CHAPTER).path
    pages = [page for page in text(path) if page.strip()]
    left = text(path, 0, 180, 310, 390)
    right = text(path, 310, 180, 302, 390)
    roster = chapters()
    if len(roster) != len(pages):
        raise ValueError(f"{len(roster)} chapters in the participation report, {len(pages)} pages of results")
    contests: dict[str, Contest] = {}
    for index, page in enumerate(pages):
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        at = next(i for i, line in enumerate(lines) if line.endswith("AGENCY"))
        agency, title = lines[at], lines[at + 1]
        code, name, _, _ = roster[index]
        if fold(title.removesuffix(" CHAPTER"))[:4] != fold(name)[:4]:
            raise ValueError(f"page {index + 1}: {title} is not chapter {code} {name}")
        for heading, rows in blocks(left[index]) + blocks(right[index]):
            entry = contests.setdefault(heading, {"heading": heading, "units": {}, "agency": agency})
            units: dict[str, dict[str, int]] = entry["units"]  # ty: ignore[invalid-assignment]
            if code in units:
                raise ValueError(f"{heading} twice in chapter {code}")
            units[code] = dict(rows)
    return contests


def words(text: str) -> str:
    seat = SCHOOL_SEAT.match(text)
    if seat:
        return f"{words(seat[1])} {seat[2]}"
    return re.sub(r"(?:^|(?<=[\s/(-]))(\w)", lambda found: found[1].upper(), text.lower()).replace(" And ", " and ")


def classify(heading: str) -> tuple[str, str, str]:
    parts = heading.split(" / ")
    if parts[0] == "Navajo Nation President/Vice President":
        return "president", "", ""
    if parts[0] in ("Navajo Board of Election Supervisors", "Navajo Nation Board of Education"):
        agency = words(re.sub(r"(?i)^at large with ", "", parts[1]))
        return "supervisors" if "Supervisors" in parts[0] else "education", agency, agency
    if parts[0] == "Navajo Nation Council":
        listed = re.sub(r"(?i)^representing\s+", "", " ".join(parts[1:]))
        return "council", listed, words(re.sub(r"(?i)\s+chapters?$", "", listed))
    office = re.match(r"^(.*?) CHAPTER (VICE PRESIDENT|PRESIDENT)$", " ".join(parts))
    if office:
        return "chapter", office[2], words(office[1])
    if parts[0] == "COMMISSIONER - NASCHITTI":
        return "commissioner", "Naschitti", "Naschitti"
    school = SCHOOL_NAMES.get(parts[0], parts[0])
    scope = re.sub(r"(?i)^at large with\s+", "", " ".join(parts[1:]))
    return "school", school, words(re.sub(r"(?i)(\s+chapters?|\s+chapte)+$", "", scope))


def board(school: str) -> str:
    seat = SCHOOL_SEAT.match(school)
    title = words(seat[1] if seat else school)
    return title if title.endswith("Board") else f"{title} Board"


def certified() -> dict[frozenset[str], dict[str, int]]:
    groups: dict[tuple[str, str], dict[str, int]] = {}
    for line in CERTIFIED_TOTALS.read_text().splitlines()[1:]:
        document, _, group, candidate, votes, _ = line.split("\t")
        groups.setdefault((document, group), {})[fold(candidate)] = int(votes)
    return {frozenset(votes): votes for votes in groups.values()}


def emit() -> Path:
    contests = parse()
    roster = chapters()
    totals_by_set = certified()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Navajo Nation general election of November 8, 2022 by chapter from the Navajo Election Administration: president and vice president, Navajo Nation Council, Board of Election Supervisors, Board of Education, school boards, and chapter officials",
            polity="US",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        nation = cs.key("us.polity", POLITY)
        if nation in cs.minted:
            cs.add("polity", id=nation, kind="tribal_nation", iso_code=None)
            name(nation, NATION)
            cs.add("polity_parent", polity_id=nation, parent_id=us)
            cs.add("slug", path="us/navajo-nation", entity_id=nation)
        if conn.execute("select 1 from scheme where code = %s", [SCHEME]).fetchone() is None:
            cs.add("scheme", code=SCHEME, polity_id=us, kind="administrative", rank=3, noun="chapter")
        units: dict[str, UUID] = {}
        titles: dict[str, str] = {}
        for code, title, _, _ in roster:
            titles[code] = title
            area = cs.area(SCHEME, code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=SCHEME)
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                name(area, f"{title} Chapter")
            units[code] = cs.unit(SCHEME, code)
            if units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=nation,
                    scheme_code=SCHEME,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
        remainder_code = f"navajo:{DAY}:not-by-chapter"
        remainder = cs.unit("us.aggregate", remainder_code)
        if remainder in cs.minted:
            cs.add(
                "reporting_unit",
                id=remainder,
                polity_id=nation,
                scheme_code="us.aggregate",
                code=remainder_code,
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
            name(remainder, "Votes not reported by chapter")
        election = cs.key("us.election", f"{DAY}:{POLITY}")
        cs.add(
            "election",
            id=election,
            polity_id=nation,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, f"{DAY.year} Navajo Nation general election")
        cs.add("slug", path=f"us/navajo-nation/{DAY}-general", entity_id=election)
        title = f"{DAY.year} Navajo Nation General Election results"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGE,
                license="Public record",
            )
        for url in (PAGE, BY_CHAPTER, PARTICIPATION, *CERTIFIED):
            cs.artifact(raw.cached(SOURCE, url), source)
        fptp = cs.key("us.electoral_system", "fptp")
        offices: dict[str, UUID] = {}

        def office(key: str, title: str, kind: str) -> UUID:
            if key not in offices:
                offices[key] = cs.key("us.office", f"navajo-nation:{key}")
                if offices[key] in cs.minted:
                    cs.add("office", id=offices[key], polity_id=nation, kind=kind)
                    name(offices[key], title)
            return offices[key]

        office("vice-president", "Vice President of the Navajo Nation", "other")
        bodies: dict[str, UUID] = {}

        def body(key: str, title: str, kind: str) -> UUID:
            if key not in bodies:
                bodies[key] = cs.key("us.body", f"navajo-nation:{key}")
                if bodies[key] in cs.minted:
                    cs.add("body", id=bodies[key], polity_id=nation, kind=kind)
                    name(bodies[key], title)
            return bodies[key]

        persons: dict[str, UUID] = {}

        def person(display: str) -> UUID:
            key = fold(display)
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], display)
            return persons[key]

        slugs: set[str] = set()
        made = {"certified": 0, "preliminary": 0, "rows": 0}
        for heading, entry in sorted(contests.items()):
            what, detail, scope = classify(heading)
            votes: dict[str, dict[str, int]] = entry["units"]  # ty: ignore[invalid-assignment]
            owner = seat = None
            magnitude: int | None = 1
            if what == "president":
                key = f"{DAY}:{POLITY}:president"
                seat = office("president", "President of the Navajo Nation", "head_of_state")
                contest_name, contest_slug = f"{DAY.year} Navajo Nation presidential election", "president"
            elif what == "chapter":
                (code,) = votes
                label = detail.title()
                key = f"{DAY}:{POLITY}:chapter:{code}:{slug(detail)}"
                seat = office(f"chapter:{code}:{slug(detail)}", f"{label} of {titles[code]} Chapter", "other")
                contest_name = f"{DAY.year} {titles[code]} Chapter {detail.lower()} election"
                contest_slug = f"{slug(titles[code])}-chapter-{slug(detail)}"
            elif what == "commissioner":
                key = f"{DAY}:{POLITY}:commissioner:naschitti"
                seat = office("commissioner:naschitti", "Naschitti Commissioner", "other")
                magnitude = None
                contest_name, contest_slug = f"{DAY.year} Naschitti commissioner election", "naschitti-commissioner"
            elif what == "school":
                seat_match = SCHOOL_SEAT.match(detail)
                label = words(detail)
                owner = body(f"school:{slug(board(detail))}", board(detail), "council")
                magnitude = None if heading.split(" / ")[0] in WHOLE_BOARD else 1
                key = f"{DAY}:{POLITY}:school:{slug(label)}:{slug(scope)}"
                contest_name = f"{DAY.year} {label if seat_match else board(detail)} election ({scope})"
                contest_slug = f"{slug(label)}-{slug(scope)}"
            else:
                title, kind, short = BODIES[what]
                owner = body(what, title, kind)
                key = f"{DAY}:{POLITY}:{what}:{slug(detail)}"
                contest_name = f"{DAY.year} {title} election ({scope})"
                contest_slug = f"{short}-{slug(scope)}"
            contest_slug = contest_slug[:120].strip("-")
            if contest_slug in slugs:
                raise ValueError(f"slug {contest_slug} twice")
            slugs.add(contest_slug)
            labels = list(dict.fromkeys(label for rows in votes.values() for label in rows))
            chapter_sums = {label: sum(rows.get(label, 0) for rows in votes.values()) for label in labels}
            official = totals_by_set.get(frozenset(fold(label) for label in labels))
            values: dict[str, dict[str, int]] = {code: dict(rows) for code, rows in votes.items()}
            note = "Votes by chapter from the Navajo Election Administration's Results by Chapter report; no certified sheet for this race is posted, so the result stands as preliminary."
            if official is not None:
                recount = next(
                    (
                        counts
                        for prefix, counts in RECOUNTS.items()
                        if what == "council" and fold(detail).startswith(prefix)
                    ),
                    None,
                )
                if recount is not None:
                    values = {code: dict(zip(labels, pair, strict=True)) for code, pair in recount.items()}
                    note = "Votes by chapter from the recount the Navajo Election Administration attached to the certified results (hand-counted votes plus challenged and absentee ballots)."
                else:
                    gap = {label: official[fold(label)] - chapter_sums[label] for label in labels}
                    if min(gap.values()) < 0:
                        values = {remainder_code: {label: official[fold(label)] for label in labels}}
                        note = "Certified totals only: the Results by Chapter report gives a candidate more votes than the certified total, so its chapter rows are left out."
                    else:
                        if any(gap.values()):
                            values[remainder_code] = gap
                        note = "Votes by chapter from the Navajo Election Administration's Results by Chapter report. The certified totals also count ballots the report places in no chapter (such as absentee ballots); the difference sits in the unit Votes not reported by chapter, so the units sum to the certified totals."
                for label in labels:
                    if sum(rows.get(label, 0) for rows in values.values()) != official[fold(label)]:
                        raise ValueError(f"{key}: {label} does not sum to its certified total")
            stage = "preliminary" if official is None else "certified"
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if owner else "office",
                office_id=seat,
                body_id=owner,
                district_area_id=None,
                term="full",
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=fptp if magnitude == 1 else None,
                default_result_kind="counted",
            )
            name(contest, contest_name)
            if contest in cs.minted:
                cs.add("slug", path=f"us/navajo-nation/{DAY}-general/{contest_slug}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:chapter")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            options: dict[str, UUID] = {}
            for position, label in enumerate(labels, start=1):
                option = cs.key("us.option", f"{key}:{fold(label)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=label,
                )
                names = label.split(" and ") if what == "president" else [label]
                for role, display in zip(("head", "running_mate"), names, strict=False):
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{fold(label)}:{role}"),
                        person_id=person(display.strip()),
                        option_id=option,
                        role=role,
                        ballot_name=display.strip(),
                        is_independent=False,
                    )
                options[label] = option
            final = {label: sum(rows.get(label, 0) for rows in values.values()) for label in labels}
            if magnitude == 1:
                ranked = sorted(labels, key=lambda label: -final[label])
                if len(ranked) > 1 and final[ranked[0]] == final[ranked[1]]:
                    raise ValueError(f"{key}: tie")
                cs.add("outcome", result_set_id=result_set, option_id=options[ranked[0]], kind="elected", seats=1)
            for code, rows in values.items():
                unit = remainder if code == remainder_code else units[code]
                for label, count in rows.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=options[label],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=count,
                    )
                    made["rows"] += 1
            if what == "president":
                for code, _, registered, cast in roster:
                    for measure, value in ((2, registered), (3, cast)):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=units[code],
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
            made[stage] += 1
            print(f"{stage} {contest_slug}: " + ", ".join(f"{label} {final[label]}" for label in labels))
        print(made)
        return cs.write()
