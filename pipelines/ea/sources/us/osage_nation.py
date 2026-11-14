import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.osage_nation"
VERSION = "1"
DATA = Path(__file__).with_name("osage_nation.tsv")
PUBLISHER = "Osage Nation Election Office"
PAGE = "https://www.osagenation-nsn.gov/services/elections/election-information"
CDN = "https://cdn.osagenation-nsn.gov/gov-site/files/departments"
DOCS = "https://www.osagenation-nsn.gov/sites/default/files/documents"
LIBRARY = "http://www.osagenation-nsn.gov/sites/default/files/library"
SHEETS = {
    "2014-03-10": (
        f"https://web.archive.org/web/20161222112127id_/{LIBRARY}/2014PrimaryElectionCertifiedResults_0.pdf",
        "2014 Primary Election official ballot results",
    ),
    "2016-06-06": (
        f"{CDN}/Elections/2017-0331_Elections_2016-General-Election-Official-Results.jpg",
        "2016 General Election official results",
    ),
    "2017-03-20": (
        f"{CDN}/Elections/2017-0331_Elections_2017-Special-Election-Official-Election-Results.pdf",
        "2017 Special Election official results",
    ),
    "2018-06-04": (
        f"{CDN}/Elections/2018-0620_Elections_Official-Results.pdf",
        "2018 General Election official results",
    ),
    "2020-06-01": (
        f"{CDN}/election-office/2020-0625_Election-Office_General-Election-Official-Results.pdf",
        "2020 General Election official results",
    ),
    "2022-04-04": (
        f"{DOCS}/2022%20Primary%20Election%20Official%20Results.pdf",
        "2022 Primary Election official results",
    ),
    "2022-06-06": (
        f"{DOCS}/2022%20General%20Election%20Official%20Results.pdf",
        "2022 General Election official results",
    ),
    "2024-06-03": (
        f"{DOCS}/2024%20General%20Election%20Official%20Results.pdf",
        "2024 General Election official results",
    ),
    "2026-02-23": (
        f"{DOCS}/2026%20General%20Election%20Official%20Results.pdf",
        "2026 Primary Election official results",
    ),
    "2026-06-01": (f"{DOCS}/Elections_20260709_081609.pdf", "2026 General Election official results"),
}
AMENDMENTS_2017 = f"{CDN}/Elections/2017_Elections_Special-Election-Proposed-Amendments.pdf"
QUESTIONS = {
    "ONCA 16-77 (marriage definition)": (
        "ONCA 16-77: marriage definition in the Osage Nation Code",
        "Shall the definition of marriage in the Osage Nation Code at Chapter 2 of Title 9 on Domestic Relations be amended?",
        "optional_referendum",
    ),
    "ONCR 16-41 (annual budget)": (
        "ONCR 16-41: constitutional amendment on the annual budget",
        "Shall Article VI, Section 24 of the Osage Nation Constitution be amended to add language providing for a constitutionally mandated law governing the annual budgetary and appropriation process?",
        "mandatory_referendum",
    ),
    "ONCR 18-20": ("ONCR 18-20: constitutional amendment", None, "mandatory_referendum"),
    "ONCR 19-07": ("ONCR 19-07: constitutional amendment", None, "mandatory_referendum"),
    "Constitutional Amendment": ("Constitutional amendment", None, "mandatory_referendum"),
}
POLITY = "tribal:osage-nation"
NATION = "Osage Nation"
KINDS = {"primary": "primary", "general": "general", "special": "referendum"}
EXECUTIVE = {
    "Principal Chief": ("principal-chief", "Principal Chief of the Osage Nation", "head_of_state"),
    "Assistant Principal Chief": (
        "assistant-principal-chief",
        "Assistant Principal Chief of the Osage Nation",
        "other",
    ),
}
BENCH = {
    "Chief Justice": ("chief-justice", "Chief Justice of the Osage Nation Supreme Court"),
    "Associate Justice": ("associate-justice", "Associate Justice of the Osage Nation Supreme Court"),
    "Chief Judge": ("chief-judge", "Chief Judge of the Osage Nation Trial Court"),
}
PEOPLE = {
    "Geoffrey M. Standing Bear": "Geoffrey Standing Bear",
    "Raymond W. Red Corn": "Raymond Red Corn",
    "Amanda S. Proctor": "Amanda Proctor",
    "R.J. Walker": "RJ Walker",
    "Meredith (Quinata) Drent": "Meredith Drent",
    "Elizabeth Lohah Homer": "Elizabeth Homer",
    "William Ladd Oldfield": "William Oldfield",
}
NOTE = "Nation-wide totals from the Osage Nation Election Office's official results report, which splits each count into absentee, early voting (two days), and election day columns; only the totals are loaded. Undervotes and overvotes are left out, and each contest's option totals equal the report's cast votes."


def fold(text: str) -> str:
    return slug(PEOPLE.get(text, text)).replace("-", "")


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SOURCE, AMENDMENTS_2017, suffix=".pdf")
    for url, _ in SHEETS.values():
        raw.fetch(SOURCE, url, suffix=url[url.rindex(".") :])


def rows() -> dict[str, dict[str, list[dict[str, str]]]]:
    out: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    with DATA.open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            out[row["date"]][row["contest"]].append(row)
    return out


def emit() -> Path:
    data = rows()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Osage Nation primary, general, and special elections 2014 to 2026 from the Osage Nation Election Office's official results: Principal Chief, Assistant Principal Chief, Osage Nation Congress, judicial retention, and referendums, nation-wide totals",
            polity="US",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        nation = cs.key("us.polity", POLITY)
        if nation in cs.minted:
            cs.add("polity", id=nation, kind="tribal_nation", iso_code=None)
            name(nation, NATION)
            cs.add("polity_parent", polity_id=nation, parent_id=cs.polity("US"))
            cs.add("slug", path="us/osage-nation", entity_id=nation)
        unit_code = POLITY
        unit = cs.unit("us.aggregate", unit_code)
        if unit in cs.minted:
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=nation,
                scheme_code="us.aggregate",
                code=unit_code,
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
                valid_from=None,
            )
            name(unit, "Osage Nation (all ballots)")
        systems = {code: cs.key("us.electoral_system", code) for code in ("fptp", "block", "retention", "referendum")}
        congress = cs.key("us.body", "osage-nation:congress")
        if congress in cs.minted:
            cs.add("body", id=congress, polity_id=nation, kind="unicameral")
            name(congress, "Osage Nation Congress")
        offices: dict[str, UUID] = {}

        def office(key: str, title: str, kind: str) -> UUID:
            if key not in offices:
                offices[key] = cs.key("us.office", f"osage-nation:{key}")
                if offices[key] in cs.minted:
                    cs.add("office", id=offices[key], polity_id=nation, kind=kind)
                    name(offices[key], title)
            return offices[key]

        persons: dict[str, UUID] = {}

        def person(display: str) -> UUID:
            key = fold(display)
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], PEOPLE.get(display, display))
            return persons[key]

        amendments = cs.source(PUBLISHER, "2017 Special Election proposed amendments")
        if amendments in cs.minted:
            cs.add(
                "source",
                id=amendments,
                kind="official_body",
                publisher=PUBLISHER,
                title="2017 Special Election proposed amendments",
                url=AMENDMENTS_2017,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, AMENDMENTS_2017), amendments)
        made = {"contests": 0, "values": 0}
        for day_text, contests in sorted(data.items()):
            day = date.fromisoformat(day_text)
            url, title = SHEETS[day_text]
            kind = contests[next(iter(contests))][0]["kind"]
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url), source)
            election = cs.key("us.election", f"{day}:{POLITY}:{kind}")
            cs.add(
                "election",
                id=election,
                polity_id=nation,
                kind=KINDS[kind],
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            label = {"primary": "primary election", "general": "general election", "special": "special election"}[kind]
            name(election, f"{day.year} Osage Nation {label}")
            path = f"us/osage-nation/{day}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            turnout = {row["label"]: int(row["votes"]) for row in contests.pop("*", [])}
            for heading, entries in contests.items():
                page = int(entries[0]["page"])
                magnitude = int(entries[0]["seats"])
                votes = {row["label"]: int(row["votes"]) for row in entries if not row["label"].startswith("#")}
                cast = next((int(row["votes"]) for row in entries if row["label"] == "#cast"), None)
                if cast is not None and cast != sum(votes.values()):
                    raise ValueError(f"{day} {heading}: options sum to {sum(votes.values())}, report says {cast}")
                seat = body = None
                question = heading in QUESTIONS
                retention = heading.startswith("Retain ")
                if question:
                    contest_name, text, question_type = QUESTIONS[heading]
                    contest_name = f"{day.year} Osage Nation referendum: {contest_name}"
                    contest_slug = slug(heading.split(" (")[0])
                    system = systems["referendum"]
                elif retention:
                    rank, judge = next(
                        (rank, heading.removeprefix(f"Retain {rank} "))
                        for rank in BENCH
                        if heading.startswith(f"Retain {rank} ")
                    )
                    office_key, office_title = BENCH[rank]
                    seat = (
                        office(f"{office_key}:{fold(judge)}", f"{office_title} ({PEOPLE.get(judge, judge)})", "other")
                        if rank == "Associate Justice"
                        else office(office_key, office_title, "other")
                    )
                    contest_name = f"{day.year} {office_title} retention: {PEOPLE.get(judge, judge)}"
                    contest_slug = f"retention-{slug(PEOPLE.get(judge, judge))}"
                    system = systems["retention"]
                elif heading == "Congress":
                    body = congress
                    contest_name = f"{day.year} Osage Nation Congress {label}"
                    contest_slug = "congress"
                    system = systems["block"]
                else:
                    office_key, office_title, office_kind = EXECUTIVE[heading]
                    seat = office(office_key, office_title, office_kind)
                    contest_name = f"{day.year} {office_title} {label}"
                    contest_slug = office_key
                    system = systems["fptp"]
                key = f"{day}:{POLITY}:{contest_slug}"
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question" if question else "body_seats" if body else "office",
                    office_id=seat,
                    body_id=body,
                    district_area_id=None,
                    term=None if question else "full",
                    magnitude=None if question else magnitude,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, contest_name)
                cs.add("slug", path=f"{path}/{contest_slug}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source, locator={"page": page})
                if question:
                    cs.add("ballot_question", contest_id=contest, type=question_type)
                    if text:
                        cs.add(
                            "ballot_question_text",
                            contest_id=contest,
                            kind="text",
                            lang="en",
                            body=text,
                            url=AMENDMENTS_2017,
                            source_id=amendments,
                            locator={"page": 1},
                        )
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
                options: dict[str, UUID] = {}
                for position, option_label in enumerate(votes, start=1):
                    answer = question or retention
                    option = cs.key("us.option", f"{key}:{fold(option_label)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer" if answer else "candidate",
                        ballot_position=position,
                        ballot_label=option_label,
                    )
                    if not answer or (retention and option_label == "Yes"):
                        display = judge if retention else option_label
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{fold(display)}:head"),
                            person_id=person(display),
                            option_id=option,
                            role="head",
                            ballot_name=display,
                            is_independent=False,
                        )
                    options[option_label] = option
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes[option_label],
                    )
                    made["values"] += 1
                for measure, value in ((2, turnout.get("#registered")), (3, turnout.get("#ballots"))):
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
                if question or retention:
                    yes, no = (options[label] for label in options)
                    passed = votes[next(iter(votes))] > votes[list(votes)[1]]
                    if retention:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=yes,
                            kind="elected" if passed else "lost",
                            seats=1 if passed else None,
                        )
                    else:
                        cs.add("outcome", result_set_id=result_set, option_id=yes, kind="won" if passed else "lost")
                        cs.add("outcome", result_set_id=result_set, option_id=no, kind="lost" if passed else "won")
                else:
                    ranked = sorted(votes, key=lambda option_label: -votes[option_label])
                    winners = 2 if kind == "primary" else magnitude
                    if len(ranked) > winners and votes[ranked[winners - 1]] == votes[ranked[winners]]:
                        raise ValueError(f"{key}: tie at the last place")
                    for option_label in ranked[:winners]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=options[option_label],
                            kind="advanced" if kind == "primary" else "elected",
                            seats=None if kind == "primary" else 1,
                        )
                made["contests"] += 1
                print(f"{day} {contest_slug}: " + ", ".join(f"{k} {v}" for k, v in votes.items()))
        print(made)
        return cs.write()
