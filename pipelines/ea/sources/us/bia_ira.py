import csv
import html
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.tribal import KEYS
from ea.store import connect

SOURCE = "us.bia_ira"
VERSION = "1"
PUBLISHER = "United States Indian Service"
TITLE = "Ten Years of Tribal Government under I.R.A., by Theodore H. Haas (Tribal Relations Pamphlets 1, 1947), Table A: Indian tribes, bands, and communities which voted to accept or reject the terms of the Indian Reorganization Act"
TABLE = "https://thorpe.law.ou.edu/IRA/IRAbook/tribalgovpt1tblA.htm"
NATIONS = Path(__file__).with_name("bia_ira_tribes.tsv")
STATES = {
    "ARIZONA": "AZ",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "FLORIDA": "FL",
    "IDAHO": "ID",
    "IOWA": "IA",
    "KANSAS": "KS",
    "LOUISIANA": "LA",
    "MINNESOTA": "MN",
    "MICHIGAN": "MI",
    "MISSISSIPPI": "MS",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OREGON": "OR",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "UTAH": "UT",
    "WASHINGTON": "WA",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
}
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Oct": 10, "Nov": 11, "Dec": 12}
DAY = re.compile(r"^([A-Z][a-z]+)\.? ?(\d+)(?:-(\d+))?(?:[.,](\d{4}))?$")
FOOTNOTES = {
    "*": "Indians residing on lands purchased from I.R.A. funds. Group is organized under the I.R.A.",
    "**": "Act applies since Indians did not vote against it.",
    "***": "Act applies since less than 30 percent of eligible voters participated in the election.",
}
MISDATED = {("NM", "Laguna"): "Oct.27.1935"}


def fetch() -> None:
    raw.fetch(SOURCE, TABLE, suffix=".htm")


def number(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def when(text: str) -> tuple[date, date, str]:
    if not text:
        return date(1935, 6, 1), date(1935, 6, 30), "month"
    found = DAY.match(text.replace(" ", ""))
    if found is None:
        raise ValueError(f"date {text!r}")
    month = MONTHS[found[1][:3]]
    year = 1934 if month >= 10 else 1935
    first = date(year, month, int(found[2]))
    last = date(year, month, int(found[3])) if found[3] else first
    return first, last, "day"


def rows() -> list[dict[str, object]]:
    body = raw.cached(SOURCE, TABLE).path.read_text(encoding="latin-1")
    out: list[dict[str, object]] = []
    state = agency = ""
    pending: dict[str, object] | None = None
    for line in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", line, re.DOTALL | re.IGNORECASE)
        texts = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip() for cell in cells]
        if texts and texts[0] in STATES:
            state = STATES[texts[0]]
            continue
        if len(texts) < 2 or texts[1] == "RESERVATION":
            continue
        if texts[0]:
            agency = texts[0]
        printed = texts[1]
        reservation = printed.lstrip("*")
        marks = printed[: len(printed) - len(reservation)]
        reservation = reservation.rstrip(". ").strip()
        bold = bool(re.search(r"<b>\s*\**[A-Za-z]", cells[1], re.IGNORECASE))
        if reservation == "Standing Rock":
            pending = {"bold": bold, "marks": marks}
            continue
        if reservation.startswith("(") and pending is not None:
            if len(texts) < 7 or texts[4] == "":
                continue
            reservation, bold, marks = "Standing Rock", bool(pending["bold"]), str(pending["marks"])
        if not reservation and agency.startswith("Navajo"):
            reservation = "Navajo"
        if len(texts) < 7:
            continue
        yes, no = number(texts[4]), number(texts[5])
        if yes is None or no is None:
            continue
        explicit = re.search(r"(\d{4})$", texts[6])
        first, last, precision = when(texts[6])
        if explicit and int(explicit[1]) != first.year and MISDATED.get((state, reservation)) != texts[6]:
            raise ValueError(f"{reservation}: {texts[6]} against {first}")
        out.append(
            {
                "state": state,
                "agency": agency.rstrip(":").replace(" (cont.)", "").replace(" (cont)", ""),
                "reservation": reservation,
                "eligible": number(texts[3]),
                "yes": yes,
                "no": no,
                "printed_date": texts[6],
                "first": first,
                "last": last,
                "precision": precision,
                "accepted": bold,
                "marks": marks,
            }
        )
    return out


def emit() -> Path:
    with NATIONS.open() as handle:
        nations = {(row["state"], row["reservation"]): row["nation"] for row in csv.DictReader(handle, delimiter="\t")}
    data = rows()
    seen = {(str(row["state"]), str(row["reservation"])) for row in data}
    if seen != set(nations):
        raise ValueError(f"unmapped {sorted(seen - set(nations))}, stale {sorted(set(nations) - seen)}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indian Reorganization Act acceptance referendums of 1934 and 1935 by reservation, from Haas, Ten Years of Tribal Government under I.R.A. (U.S. Indian Service, 1947), Table A, under each present-day tribal nation",
            polity="US",
        )
        paths = dict(
            conn.execute(
                "select s.entity_id, s.path from slug s join polity p on p.id = s.entity_id where p.kind = 'tribal_nation'"
            ).fetchall()
        )

        def label(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=TABLE,
                license="Public domain (U.S. government work)",
            )
        cs.artifact(raw.cached(SOURCE, TABLE), source)
        groups: dict[tuple[str, date, date], list[dict[str, object]]] = defaultdict(list)
        for row in data:
            nation = nations[(str(row["state"]), str(row["reservation"]))]
            if nation:
                groups[(nation, row["first"], row["last"])].append(row)  # ty: ignore[invalid-argument-type]
        made = {"elections": 0, "contests": 0, "nations": len({nation for nation, _, _ in groups})}
        for (nation, first, last), members in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0])):
            base = KEYS.get(nation) or slug(nation.removeprefix("The "))
            polity = cs.key("us.polity", f"tribal:{base}")
            if polity in cs.minted:
                raise ValueError(f"{nation} is not held")
            path = paths[polity]
            precision = str(members[0]["precision"])
            election_key = f"{first}:tribal:{base}:ira"
            election = cs.key("us.election", election_key)
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                kind="referendum",
                date_from=first,
                date_to=last,
                date_precision=precision,
                status="held",
            )
            places = [str(row["reservation"]) for row in members]
            listed = (
                places[0]
                if len(places) == 1
                else ", ".join(places[:-1]) + (" and " if len(places) == 2 else ", and ") + places[-1]
            )
            label(election, f"{first.year} Indian Reorganization Act referendum ({listed})")
            stem = (
                f"{first}-indian-reorganization-act-referendum"
                if precision == "day"
                else f"{first:%Y-%m}-indian-reorganization-act-referendum"
            )
            election_slug = f"{path}/{stem}"
            cs.add("slug", path=election_slug, entity_id=election)
            made["elections"] += 1
            for row in members:
                reservation = str(row["reservation"])
                place = slug(reservation)
                key = f"{election_key}:{place}"
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    office_id=None,
                    body_id=None,
                    district_area_id=None,
                    term=None,
                    magnitude=None,
                    round_number=1,
                    electoral_system_id=None,
                    default_result_kind="counted",
                )
                label(contest, f"{first.year} {reservation} vote on the Indian Reorganization Act")
                cs.add("slug", path=f"{election_slug}/{place}", entity_id=contest)
                locator = {
                    "url": TABLE,
                    "table": "A",
                    "state": row["state"],
                    "agency": row["agency"],
                    "row": reservation,
                }
                cs.add("citation", entity_id=contest, source_id=source, locator=locator)
                cs.add("ballot_question", contest_id=contest, type="referendum")
                unit = cs.unit("us.aggregate", f"ira:{base}:{place}")
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.aggregate",
                        code=f"ira:{base}:{place}",
                        area_relation="unlocated",
                        area_id=None,
                        kind="aggregate",
                        valid_from=None,
                        valid_to=None,
                    )
                    label(unit, f"{reservation} ({row['agency']}), all voters")
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                yes, no = int(str(row["yes"])), int(str(row["no"]))
                eligible = row["eligible"] if isinstance(row["eligible"], int) else None
                accepted = bool(row["accepted"])
                notes = [
                    f"Votes for and against accepting the Indian Reorganization Act (48 Stat. 984, section 18), as printed for {reservation} under the {row['agency']} in Haas (1947), Table A.",
                    "The outcome is the table's: entries in boldface accepted the Act.",
                ]
                if eligible is not None and eligible < yes + no:
                    notes.append(
                        f"The table prints a voting population of {eligible}, fewer than the {yes + no} votes cast, so it is not stored."
                    )
                    eligible = None
                if precision == "month":
                    notes.append(
                        "The table prints no election date; the Sacramento Agency rancherias voted in June 1935."
                    )
                if (row["state"], reservation) in MISDATED:
                    notes.append(
                        f"The table prints the date as {row['printed_date']}; the Act's elections closed in June 1935, so the year is read as 1934."
                    )
                if row["marks"]:
                    notes.append(f"The table marks the entry {row['marks']}: {FOOTNOTES[str(row['marks'])]}")
                if accepted != (yes > no):
                    notes.append(
                        f"The table declares the Act {'accepted' if accepted else 'rejected'} although {'more' if no > yes else 'as many'} votes were cast against it."
                    )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=" ".join(notes))
                options: dict[str, UUID] = {}
                for position, (answer, count) in enumerate((("Yes", yes), ("No", no)), start=1):
                    option = cs.key("us.option", f"{key}:{answer.lower()}")
                    options[answer] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=answer,
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
                        value=count,
                    )
                if eligible is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=2,
                        count_round=0,
                        flags=0,
                        value=eligible,
                    )
                cs.add(
                    "outcome", result_set_id=result_set, option_id=options["Yes"], kind="won" if accepted else "lost"
                )
                cs.add("outcome", result_set_id=result_set, option_id=options["No"], kind="lost" if accepted else "won")
                made["contests"] += 1
                print(f"{first} {nation} / {reservation}: {yes}-{no} {'accepted' if accepted else 'rejected'}")
        skipped = sorted(
            f"{row['state']} {row['reservation']}"
            for row in data
            if not nations[(str(row["state"]), str(row["reservation"]))]
        )
        print(f"no present-day nation: {', '.join(skipped)}")
        print(made)
        return cs.write()
