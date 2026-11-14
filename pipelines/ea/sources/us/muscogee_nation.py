import csv
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.muscogee_nation"
VERSION = "1"
PUBLISHER = "Muscogee (Creek) Nation Election Board"
PAGE = "https://www.muscogeenation.com/election-board/"
UPLOADS = "https://www.muscogeenation.com/wp-content/uploads/"
POLITY = "tribal:muscogee-creek-nation"
NATION = "Muscogee (Creek) Nation"
PATH = "us/muscogee-creek-nation"
UNIT = "muscogee-creek-nation"
SCANS = Path(__file__).with_name("muscogee_nation_scans.tsv")
TYPED = [
    f"2022/08/{name}.pdf"
    for name in (
        "1979-Constitution-Vote",
        "1979-Principal-Officers-Election",
        "1980-Run-Off-Election",
        "1991-Primary-Election-10.40.23-AM",
        "1991-General-Election",
        "1993-Special-Primary-Election",
        "1993-Special-General-Election",
        "1993-Primary-Election",
        "1993-General-Election",
        "1995-Special-General-Election",
        "1995-Special-Election",
        "1995-Primary-Election",
        "1995-General-Election",
        "1996-PRIMARY-ELECTION",
        "1996-GENERAL-ELECTION",
        "1997-PRIMARY-ELECTION",
        "1997-GENERAL-ELECTION",
        "1999-PRIMARY-ELECTION",
        "1999-GENERAL-ELECTION",
        "2001-PRIMARY-ELECTION",
        "2001-GENERAL-ELECTION",
        "2002-SPECIAL-ELECTION",
        "2003-SPECIAL-ELECTION",
        "2003-PRIMARY-ELECTION",
        "2003-GENERAL-ELECTION",
        "2005-SPECIAL-PRIMARY-ELECTION",
        "2005-SPECIAL-GENERAL-ELECTION",
        "2005-PRIMARY-ELECTION",
        "2005-GENERAL-ELECTION",
        "2006-SPECIAL-ELECTION",
        "2007-PRIMARY-ELECTION",
        "2007-GENERAL-ELECTION",
        "2009-PRIMARY-ELECTION",
        "2009-GENERAL-ELECTION",
        "2009-SPECIAL-PRIMARY-ELECTION",
        "2009-SPECIAL-GENERAL-ELECTION",
        "2011-PRIMARY-ELECTION",
        "2011-GENERAL-ELECTION",
        "2012-SPECIAL-PRIMARY-ELECTION",
        "2012-SPECIAL-GENERAL-ELECTION",
        "2013-PRIMARY-ELECTION",
        "2013-GENERAL-ELECTION",
        "2014-SPECIAL-PRIMARY-ELECTION",
        "2015-SPECIAL-GENERAL-ELECTION",
        "2015-PRIMARY-ELECTION",
        "2015-GENERAL-ELECTION",
        "2017-PRIMARY-ELECTION",
        "2017-GENERAL-ELECTION",
        "2019-PRIMARY-ELECTION",
        "2019-GENERAL-ELECTION",
        "2021-PRIMARY-ELECTION",
        "2021-GENERAL-ELECTION",
    )
]
SCANNED = {
    "2023/12/Official-Results-2023-General-Election.pdf": date(2023, 11, 4),
    "2025/09/2025-General-Official-Results.pdf": date(2025, 9, 20),
    "2026/06/2026-MCN-SE-Results-OFFICIAL-260608.pdf": date(2026, 5, 30),
}
PREVIOUS = {
    "1980-Run-Off-Election": "1979-Principal-Officers-Election",
    "1993-Special-General-Election": "1993-Special-Primary-Election",
    "2005-SPECIAL-GENERAL-ELECTION": "2005-SPECIAL-PRIMARY-ELECTION",
    "2009-SPECIAL-GENERAL-ELECTION": "2009-SPECIAL-PRIMARY-ELECTION",
    "2012-SPECIAL-GENERAL-ELECTION": "2012-SPECIAL-PRIMARY-ELECTION",
    "2015-SPECIAL-GENERAL-ELECTION": "2014-SPECIAL-PRIMARY-ELECTION",
}
DISTRICTS = {"Akfvske": "Okfuskee", "Kowetv": "Wagoner", "Mvskoke": "Muskogee", "Okmulke": "Okmulgee", "Tvlse": "Tulsa"}
DAY = re.compile(r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day, (\w+ \d+, \d{4})")
COUNTED = re.compile(r"^(\S.*?\S)\s{2,}([\d,]+)\s+[\d.]+\s*%$")
DECLARED = re.compile(r"^(\S.*?)(?:,| -)\s*(Winner|Unopposed, Winner|Winner, Unopposed|Court Order, Winner)$")
SEAT = re.compile(r"^National Council Seat ([A-G] )?- (\w+) District$")
CHANNELS = {"all": 0, "election_day": 1, "early": 2, "postal": 3}
TACIT = {"Unopposed, Winner", "Winner, Unopposed", "Court Order, Winner"}
SPOILED = {"Undervotes": 6, "Overvotes": 5}

Contest = dict[str, object]


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", slug(text))


def name(doc: str) -> str:
    return doc.rsplit("/", 1)[1].removesuffix(".pdf")


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for doc in (*TYPED, *SCANNED):
        raw.fetch(SOURCE, UPLOADS + doc, suffix=".pdf")


def words(text: str) -> str:
    def part(word: str) -> str:
        mixed = re.match(r"^(Mc|La)([A-Z]{2,}.*)$", word)
        if mixed:
            return mixed[1] + mixed[2].capitalize()
        if not word.isupper() or word in ("II", "III", "IV") or re.fullmatch(r"(?:[A-Z]\.)+", word):
            return word
        return re.sub(r"[A-Z]+", lambda found: found[0].capitalize(), word)

    return " ".join("-".join(part(piece) for piece in word.split("-")) for word in text.split())


def same(one: str, other: str) -> bool:
    last, other_last = fold(one.split()[-1]), fold(other.split()[-1])
    return len(min(last, other_last)) >= 5 and (last.startswith(other_last) or other_last.startswith(last))


def typed(doc: str) -> tuple[date, list[Contest]]:
    path = raw.cached(SOURCE, UPLOADS + doc).path
    body = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found = DAY.search(body)
    if found is None:
        raise ValueError(f"{doc}: no date")
    day = datetime.strptime(found[1], "%B %d, %Y").date()
    contests: list[Contest] = []
    recount = False
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("Muscogee (Creek) Nation", "Saturday,", "Wednesday,")):
            continue
        if "RECOUNT" in line:
            recount = True
            continue
        if line.endswith(("ELECTION", "OFFICIAL RESULTS", "VOTE")) and line.isupper():
            continue
        counted = COUNTED.match(line)
        declared = DECLARED.match(line)
        if counted:
            contests[-1]["rows"].append((counted[1], int(counted[2].replace(",", "")), ""))  # ty: ignore[unresolved-attribute]
        elif declared:
            contests[-1]["rows"].append((declared[1], None, declared[2]))  # ty: ignore[unresolved-attribute]
        elif (
            contests
            and not contests[-1]["totals"]
            and re.fullmatch(r"[A-Z][A-Za-z .'\"()-]+", line)
            and not re.search(r"[a-z]{2}", line)
        ):
            contests[-1]["rows"].append((line, None, ""))  # ty: ignore[unresolved-attribute]
        else:
            heading = re.sub(r"\s{2,}(Total|TOTAL)$", "", line)
            contests.append(
                {"heading": heading, "rows": [], "recount": recount, "channels": {}, "totals": heading != line}
            )
    return day, contests


def scanned() -> dict[str, list[Contest]]:
    out: dict[str, list[Contest]] = {}
    for row in csv.DictReader(SCANS.open(), delimiter="\t"):
        contests = out.setdefault(row["document"], [])
        if not contests or contests[-1]["heading"] != row["contest"]:
            contests.append({"heading": row["contest"], "rows": [], "recount": False, "channels": {}})
        current = contests[-1]
        channels = {key: int(row[key]) for key in CHANNELS if row[key]}
        if row["option"] in SPOILED:
            current["channels"][row["option"]] = channels  # ty: ignore[invalid-assignment]
            continue
        current["rows"].append((row["option"], channels.pop("all"), row["outcome"]))  # ty: ignore[unresolved-attribute]
        if channels:
            current["channels"][row["option"]] = channels  # ty: ignore[invalid-assignment]
    return out


def what(heading: str) -> tuple[str, str, str]:
    if heading in ("Principal Chief", "Second Chief"):
        return "office", slug(heading), heading
    seat = SEAT.match(heading)
    if seat:
        district = DISTRICTS.get(seat[2], seat[2])
        letter = (seat[1] or "").strip()
        label = f"Seat {letter} – {seat[2]} District" if letter else f"{seat[2]} District"
        return "council", slug(f"{letter} {district}".strip()), label
    question = re.sub(r"^(Tribal Question|National Council Referendum No\.) ", "", heading)
    return "question", slug(question.replace("??_??", "unnumbered")), heading


def kind_of(doc: str, contests: list[Contest]) -> str:
    title = name(doc).lower()
    if "run-off" in title:
        return "runoff"
    if all(what(str(contest["heading"]))[0] == "question" for contest in contests):
        return "referendum"
    if "special" in title or "-se-" in title:
        return "by_election"
    if "primary" in title:
        return "primary"
    return "general"


def emit() -> Path:
    docs: list[tuple[str, date, list[Contest]]] = [(doc, *typed(doc)) for doc in TYPED]
    scans = scanned()
    docs += [(doc, day, scans[doc]) for doc, day in SCANNED.items()]
    previous_of = dict(PREVIOUS)
    for doc, _, _ in docs:
        base = name(doc)
        if base.endswith(("GENERAL-ELECTION", "General-Election")) and "SPECIAL" not in base.upper():
            primary = next(
                (
                    name(other)
                    for other in TYPED
                    if name(other).startswith(base[:4])
                    and "PRIMARY" in name(other).upper()
                    and "SPECIAL" not in name(other).upper()
                ),
                None,
            )
            if primary:
                previous_of.setdefault(base, primary)
    following = {before: after for after, before in previous_of.items()}
    by_name = {name(doc): (doc, day, contests) for doc, day, contests in docs}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Muscogee (Creek) Nation elections 1979-2026 from the Election Board's official results: principal chief, second chief, National Council seats, and constitutional questions",
            polity="US",
        )

        def label(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        nation = cs.key("us.polity", POLITY)
        if nation in cs.minted:
            cs.add("polity", id=nation, kind="tribal_nation", iso_code=None)
            label(nation, NATION)
            cs.add("polity_parent", polity_id=nation, parent_id=us)
            cs.add("slug", path=PATH, entity_id=nation)
        unit = cs.unit("us.aggregate", UNIT)
        if unit in cs.minted:
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=nation,
                scheme_code="us.aggregate",
                code=UNIT,
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
                valid_from=None,
                valid_to=None,
            )
            label(unit, f"{NATION}, all voters")
        title = "Previous elections: official results"
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
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        two_round = cs.key("us.electoral_system", "two_round")
        offices: dict[str, UUID] = {}
        for key, title_text, office_kind in (
            ("principal-chief", "Principal Chief of the Muscogee (Creek) Nation", "head_of_state"),
            ("second-chief", "Second Chief of the Muscogee (Creek) Nation", "other"),
        ):
            offices[key] = cs.key("us.office", f"muscogee-creek-nation:{key}")
            if offices[key] in cs.minted:
                cs.add("office", id=offices[key], polity_id=nation, kind=office_kind)
                label(offices[key], title_text)
        council = cs.key("us.body", "muscogee-creek-nation:national-council")
        if council in cs.minted:
            cs.add("body", id=council, polity_id=nation, kind="unicameral")
            label(council, "Muscogee (Creek) Nation National Council")
        persons: dict[str, UUID] = {}

        def person(display: str, scope: str) -> UUID:
            key = fold(display) if " " in display else f"{scope}:{fold(display)}"
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    label(persons[key], display)
            return persons[key]

        def value(result_set: UUID, option: UUID | None, measure: int, channel: int, count: int) -> None:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=channel,
                measure=measure,
                count_round=0,
                flags=0,
                value=count,
            )

        made: dict[str, int] = {}
        contest_ids: dict[tuple[str, str], UUID] = {}
        for doc, day, contests in sorted(docs, key=lambda item: item[1]):
            base = name(doc)
            kind = kind_of(doc, contests)
            election_key = f"{day}:{POLITY}:{kind}"
            election = cs.key("us.election", election_key)
            cs.add(
                "election",
                id=election,
                polity_id=nation,
                kind=kind,
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            noun = {
                "runoff": "runoff election",
                "referendum": "special election" if day.year != 1979 else "constitution ratification vote",
                "by_election": "special primary election" if "PRIMARY" in base.upper() else "special election",
                "primary": "primary election" if day.year != 1979 else "principal officers election",
                "general": "general election",
            }[kind]
            if base == "1979-Principal-Officers-Election":
                noun = "principal officers election"
            elif kind == "by_election" and "GENERAL" in base.upper():
                noun = "special general election"
            label(election, f"{day.year} {NATION} {noun}")
            election_slug = f"{PATH}/{day}-{slug(noun)}"
            cs.add("slug", path=election_slug, entity_id=election)
            artifact = raw.cached(SOURCE, UPLOADS + doc)
            cs.artifact(artifact, source)
            before = by_name.get(previous_of.get(base, ""))
            after = by_name.get(following.get(base, ""))
            seen: set[str] = set()
            for contest_data in contests:
                heading = str(contest_data["heading"])
                rows: list[tuple[str, int | None, str]] = contest_data["rows"]  # ty: ignore[invalid-assignment]
                channels: dict[str, dict[str, int]] = contest_data["channels"]  # ty: ignore[invalid-assignment]
                category, seat, display = what(heading)
                recount = bool(contest_data["recount"])
                tacit = any(why in TACIT for _, _, why in rows)
                key = f"{day}:{POLITY}:{category}:{seat}"
                contest_slug = seat if category == "office" else f"council-{seat}" if category == "council" else seat
                if category == "question" and not re.search(r"\d", seat):
                    contest_slug = f"question-{seat}"
                contest = cs.key("us.contest", key)
                if not recount:
                    if contest_slug in seen:
                        raise ValueError(f"{doc}: {heading} twice")
                    seen.add(contest_slug)
                    previous = None
                    if before is not None:
                        previous = contest_ids.get((name(before[0]), f"{category}:{seat}"))
                    contest_ids[(base, f"{category}:{seat}")] = contest
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind={"office": "office", "council": "body_seats", "question": "ballot_question"}[category],
                        office_id=offices.get(seat) if category == "office" else None,
                        body_id=council if category == "council" else None,
                        district_area_id=None,
                        term="full" if category != "question" else None,
                        magnitude=1 if category != "question" else None,
                        round_number=2 if previous else 1,
                        previous_round_id=previous,
                        electoral_system_id=two_round if category != "question" else None,
                        default_result_kind="tacit" if tacit else "counted",
                    )
                    if category == "office":
                        contest_name = f"{day.year} {NATION} {display.lower()} {noun}"
                    elif category == "council":
                        contest_name = f"{day.year} {NATION} National Council {display} {noun}"
                    else:
                        contest_name = f"{day.year} {NATION} {display}"
                        cs.add("ballot_question", contest_id=contest, type="referendum")
                    label(contest, contest_name)
                    cs.add("slug", path=f"{election_slug}/{contest_slug}", entity_id=contest)
                    cs.add("citation", entity_id=contest, source_id=source, locator={"url": UPLOADS + doc})
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{'recount' if recount else 'official'}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="recount" if recount else "certified",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=recount
                    or not any(other["recount"] and other["heading"] == heading for other in contests),
                    completeness=1 if any(count is not None for _, count, _ in rows) else None,
                )
                notes = [f"Official results posted by the {PUBLISHER} ({name(doc)}.pdf)."]
                if doc in SCANNED:
                    notes.append(
                        "The posted sheet is a scan; figures were read by hand and each share was checked against its printed percentage."
                    )
                if base == "2026-MCN-SE-Results-OFFICIAL-260608" and heading.endswith("26-063"):
                    notes.append(
                        "The sheet prints 41 undervotes in all, but its absentee, early, and election-day columns give 35, 4, and 0; both are stored as printed."
                    )
                if base == "1980-Run-Off-Election":
                    notes.append("The Board's sheet names the winners and prints no vote counts.")
                if any(why == "Court Order, Winner" for _, _, why in rows):
                    notes.append(
                        "The Board's sheet lists the winner as seated by court order; no vote counts are printed."
                    )
                if any("Unopposed" in why for _, _, why in rows):
                    notes.append("The candidate was unopposed and declared the winner; no vote counts are printed.")
                if recount:
                    notes.append("Official recount results of December 13, 1995.")
                cs.add("note", entity_id=result_set, kind="methodology", body_md=" ".join(notes))
                options: dict[str, UUID] = {}
                counts: dict[str, int] = {}
                outcomes: dict[str, str] = {}
                for position, (printed, count, why) in enumerate(rows, start=1):
                    answer = category == "question"
                    ballot = printed if answer else words(printed)
                    option = cs.key("us.option", f"{key}:{fold(printed)}")
                    options[printed] = option
                    if not recount:
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="answer" if answer else "candidate",
                            ballot_position=position,
                            ballot_label=ballot,
                        )
                        if not answer:
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{key}:{fold(printed)}"),
                                person_id=person(ballot, key),
                                option_id=option,
                                role="head",
                                ballot_name=ballot,
                                is_independent=False,
                            )
                    if count is not None:
                        counts[printed] = count
                        value(result_set, option, 1, 0, count)
                        for channel, number in channels.get(printed, {}).items():
                            value(result_set, option, 1, CHANNELS[channel], number)
                    if why in ("elected", "advanced", "won", "lost") or "Winner" in why:
                        outcomes[printed] = "elected" if "Winner" in why else why
                for spoiled, measure in SPOILED.items():
                    for channel, number in channels.get(spoiled, {}).items():
                        value(result_set, None, measure, CHANNELS[channel], number)
                if not outcomes and category != "question" and counts:
                    ranked = sorted(counts, key=lambda option: -counts[option])
                    total = sum(counts.values())
                    later = None
                    if after is not None:
                        later = next(
                            (other for other in after[2] if what(str(other["heading"]))[:2] == (category, seat)),
                            None,
                        )
                    if later is not None:
                        names = [option for option, _, _ in later["rows"]]  # ty: ignore[not-iterable]
                        moved = []
                        for other in names:
                            exact = [option for option in counts if fold(option) == fold(other)]
                            moved += exact or [option for option in counts if same(option, other)]
                        if len(moved) != len(names):
                            raise ValueError(f"{doc}: {heading} runs on with {names}, matched {moved}")
                        outcomes = dict.fromkeys(moved, "advanced")
                    elif len(ranked) > 1 and counts[ranked[0]] == counts[ranked[1]]:
                        raise ValueError(f"{doc}: {heading} tied")
                    elif kind in ("primary",) or (kind == "by_election" and "PRIMARY" in base.upper()):
                        if 2 * counts[ranked[0]] > total:
                            outcomes = {ranked[0]: "elected"}
                        else:
                            print(f"no outcome: {doc} {heading}")
                    else:
                        outcomes = {ranked[0]: "elected"}
                for option, outcome in outcomes.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[option],
                        kind=outcome,
                        seats=1 if outcome == "elected" else None,
                    )
                made[category] = made.get(category, 0) + (0 if recount else 1)
                print(
                    f"{day} {kind} {contest_slug}: "
                    + ", ".join(
                        f"{option} {counts.get(option, '-')}{' ' + outcomes[option] if option in outcomes else ''}"
                        for option, _, _ in rows
                    )
                )
        print(made)
        return cs.write()
