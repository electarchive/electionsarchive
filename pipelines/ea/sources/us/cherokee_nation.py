import csv
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.cherokee_nation"
VERSION = "1"
DATA = Path(__file__).with_name("cherokee_nation.tsv")
PUBLISHER = "Cherokee Nation Election Commission"
SITE = "https://election.cherokee.gov"
PAGE = f"{SITE}/elections-and-results/previous-election-results/"
DOCS = {
    "2017-06-03": ("z3ibgew3/2017-general-election-official-results.pdf", "2017 General Election official results"),
    "2017-07-22": (
        "euaayppx/2017-general-run-off-election-official-results.pdf",
        "2017 General Election Run-Off official results",
    ),
    "2019-01-05": ("1ksgne51/official-vote-count.pdf", "2019 Special Tribal Council Election official vote count"),
    "2019-06-01": ("3i4jfefa/official-vote-count-6_1_19.pdf", "2019 General Election official vote count"),
    "2019-07-27": (
        "xeya2lf4/official-vote-count-7-27-19-run-off.pdf",
        "2019 General Election Run-Off official vote count",
    ),
    "2021-06-05": ("5nqkdqky/2021-general-election-results.pdf", "2021 General Election results"),
    "2021-07-24": ("db3js0wz/2021-run-off-election-results.pdf", "2021 Run-Off Election results"),
    "2023-06-03": ("pzjbrxio/2023-elections-official-results.pdf", "2023 Elections official results"),
    "2023-07-08": ("pzjbrxio/2023-elections-official-results.pdf", "2023 Elections official results"),
    "2025-06-07": ("lxuhcpi2/2025-election-results.pdf", "2025 Election results"),
    "2025-07-26": ("lxuhcpi2/2025-election-results.pdf", "2025 Election results"),
}
POLITY = "tribal:cherokee-nation"
NATION = "Cherokee Nation"
EXECUTIVE = {
    "CHIEF": ("principal-chief", "Principal Chief of the Cherokee Nation", "head_of_state"),
    "DEPUTY CHIEF": ("deputy-principal-chief", "Deputy Principal Chief of the Cherokee Nation", "other"),
}
CHANNELS = (("absentee", 3), ("early", 2), ("election_day", 1))
PEOPLE = {
    "eojrsmith": "E. O. Smith",
    "eosmith": "E. O. Smith",
    "doralsmithpatzkowski": "Dora Patzkowski",
    "uriahlgrass": "Uriah Grass",
    "robincartermayes": "Robin Mayes",
    "brandonlgirty": "Brandon Girty",
    "cliftonehughes": "Clifton Hughes",
}
NOTE = "Nation-wide counts from the Cherokee Nation Election Commission's official cumulative report, which splits each count into absentee, early, and election day columns; those are stored as the postal, early, and election day channels. Undervotes are stored as blank and overvotes as invalid. The report gives no counts by precinct."


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", slug(text))


def display(label: str) -> str:
    if label != label.upper():
        return PEOPLE.get(fold(label), label.replace(", Jr.", " Jr."))
    words = []
    for word in label.lower().split():
        word = "-".join(part[:1].upper() + part[1:] for part in word.split("-"))
        word = re.sub(r"^Mc(\w)", lambda found: f"Mc{found[1].upper()}", word)
        words.append(word.upper() if word in ("Ii", "Iii") else word)
    text = " ".join(words)
    return PEOPLE.get(fold(text), text)


def url(day: str) -> str:
    return f"{SITE}/media/{DOCS[day][0]}"


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for day in DOCS:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def rows() -> dict[str, dict[str, list[dict[str, str]]]]:
    out: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    with DATA.open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            out[row["date"]][row["contest"]].append(row)
    return out


def kind_of(day: date) -> str:
    return "special" if day.month == 1 else "runoff" if day.month == 7 else "general"


def emit() -> Path:
    data = rows()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cherokee Nation general, run-off, and special elections 2017 to 2025 from the Cherokee Nation Election Commission's official results: Principal Chief, Deputy Principal Chief, and the Council of the Cherokee Nation by district and at large, nation-wide counts by absentee, early, and election day voting",
            polity="US",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        nation = cs.key("us.polity", POLITY)
        if nation in cs.minted:
            cs.add("polity", id=nation, kind="tribal_nation", iso_code=None)
            name(nation, NATION)
            cs.add("polity_parent", polity_id=nation, parent_id=cs.polity("US"))
            cs.add("slug", path="us/cherokee-nation", entity_id=nation)
        unit = cs.unit("us.aggregate", POLITY)
        if unit in cs.minted:
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=nation,
                scheme_code="us.aggregate",
                code=POLITY,
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
                valid_from=None,
            )
            name(unit, "Cherokee Nation (all ballots)")
        system = cs.key("us.electoral_system", "two_round")
        council = cs.key("us.body", "cherokee-nation:council")
        if council in cs.minted:
            cs.add("body", id=council, polity_id=nation, kind="unicameral")
            name(council, "Council of the Cherokee Nation")
        offices: dict[str, UUID] = {}

        def office(key: str, title: str, kind: str) -> UUID:
            if key not in offices:
                offices[key] = cs.key("us.office", f"cherokee-nation:{key}")
                if offices[key] in cs.minted:
                    cs.add("office", id=offices[key], polity_id=nation, kind=kind)
                    name(offices[key], title)
            return offices[key]

        persons: dict[str, UUID] = {}

        def person(label: str) -> UUID:
            key = fold(display(label))
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], display(label))
            return persons[key]

        first_round: dict[str, tuple[UUID, set[str]]] = {}
        sources: set[UUID] = set()
        made = {"contests": 0, "values": 0}
        for day_text, contests in sorted(data.items()):
            day = date.fromisoformat(day_text)
            kind = kind_of(day)
            title = DOCS[day_text][1]
            source = cs.source(PUBLISHER, title)
            if source in cs.minted and source not in sources:
                sources.add(source)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url(day_text),
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url(day_text)), source)
            election = cs.key("us.election", f"{day}:{POLITY}:{kind}")
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
            label = {
                "general": "general election",
                "runoff": "general run-off election",
                "special": "special election",
            }[kind]
            name(election, f"{day.year} Cherokee Nation {label}")
            path = f"us/cherokee-nation/{day}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            turnout = contests.pop("*", [])
            voters = int(turnout[0]["total"]) if turnout else None
            registered = int(turnout[0]["flag"]) if turnout and turnout[0]["flag"] else None
            whole = "CHIEF" in contests or len(contests) == 1
            for heading, entries in contests.items():
                page = int(entries[0]["page"])
                options = [row for row in entries if not row["choice"].startswith("#")]
                extra = {row["choice"]: row for row in entries if row["choice"].startswith("#")}
                for row in entries:
                    if sum(int(row[channel]) for channel, _ in CHANNELS) != int(row["total"]):
                        raise ValueError(f"{day} {heading} {row['choice']}: channels do not sum to the total")
                for channel, _ in [*CHANNELS, ("total", 0)]:
                    if sum(int(row[channel]) for row in options) != int(extra["#cast"][channel]):
                        raise ValueError(f"{day} {heading}: {channel} options do not sum to cast votes")
                seat = body = None
                if heading in EXECUTIVE:
                    office_key, office_title, office_kind = EXECUTIVE[heading]
                    seat = office(office_key, office_title, office_kind)
                    contest_name = f"{day.year} {office_title} {label}"
                    contest_slug = office_key
                else:
                    body = council
                    district = heading.removesuffix(" TRIBAL COUNCILOR")
                    scope = "at large" if district == "AT-LARGE" else district.title()
                    contest_name = f"{day.year} Council of the Cherokee Nation {label} ({scope})"
                    contest_slug = f"council-{slug(scope)}"
                key = f"{day}:{POLITY}:{contest_slug}"
                contest = cs.key("us.contest", key)
                previous = first_round.get(contest_slug) if kind == "runoff" else None
                if kind == "runoff" and previous is None:
                    raise ValueError(f"{key}: no first round")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if body else "office",
                    office_id=seat,
                    body_id=body,
                    district_area_id=None,
                    term="full" if kind != "special" else "remainder",
                    magnitude=1,
                    round_number=2 if previous else 1,
                    previous_round_id=previous[0] if previous else None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, contest_name)
                cs.add("slug", path=f"{path}/{contest_slug}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source, locator={"page": page})
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
                cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
                ids: dict[str, UUID] = {}
                for position, row in enumerate(options, start=1):
                    option = cs.key("us.option", f"{key}:{fold(row['choice'])}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        ballot_label=row["choice"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{fold(row['choice'])}:head"),
                        person_id=person(row["choice"]),
                        option_id=option,
                        role="head",
                        ballot_name=row["choice"],
                        is_independent=False,
                    )
                    ids[row["choice"]] = option
                    for channel, code in CHANNELS:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=code,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=int(row[channel]),
                        )
                        made["values"] += 1
                for label_key, measure in (("#under", 6), ("#over", 5)):
                    if label_key in extra:
                        for channel, code in CHANNELS:
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=None,
                                channel=code,
                                measure=measure,
                                count_round=0,
                                flags=0,
                                value=int(extra[label_key][channel]),
                            )
                if whole:
                    for measure, value in ((2, registered), (3, voters)):
                        if value is not None:
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=None,
                                channel=0,
                                measure=measure,
                                count_round=0,
                                flags=0,
                                value=value,
                            )
                totals = {row["choice"]: int(row["total"]) for row in options}
                cast = int(extra["#cast"]["total"])
                ranked = sorted(totals, key=lambda choice: -totals[choice])
                if totals[ranked[0]] == totals[ranked[1]]:
                    raise ValueError(f"{key}: tie at the top")
                majority = 2 * totals[ranked[0]] > cast
                people = {fold(display(choice)) for choice in ranked[:2]}
                if previous is not None:
                    if people != previous[1] or len(ranked) != 2:
                        raise ValueError(f"{key}: run-off candidates are not the first round's top two")
                    del first_round[contest_slug]
                elif not majority:
                    first_round[contest_slug] = (contest, people)
                else:
                    first_round.pop(contest_slug, None)
                for row in options:
                    if row["flag"] == "disqualified":
                        cs.add("outcome", result_set_id=result_set, option_id=ids[row["choice"]], kind="disqualified")
                if majority:
                    cs.add("outcome", result_set_id=result_set, option_id=ids[ranked[0]], kind="elected", seats=1)
                else:
                    for choice in ranked[:2]:
                        cs.add("outcome", result_set_id=result_set, option_id=ids[choice], kind="advanced")
                made["contests"] += 1
                print(
                    f"{day} {contest_slug}: "
                    + ", ".join(f"{choice} {total}" for choice, total in totals.items())
                    + ("" if majority else " (run-off)")
                )
        if first_round:
            raise ValueError(f"first rounds without a run-off: {sorted(first_round)}")
        print(made)
        return cs.write()
