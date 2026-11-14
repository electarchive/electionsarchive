import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "us.chickasaw_nation"
VERSION = "1"
DATA = Path(__file__).with_name("chickasaw_nation.tsv")
PUBLISHER = "Chickasaw Nation Election Commission"
SITE = "chickasaw.net/News/Press-Releases/Release"
RELEASES = {
    "613": (
        "20210623050906",
        "Chickasaw-Nation-Primary-Tribal-Election-Results-613",
        "Chickasaw Nation Primary Tribal Election Results (August 23, 2006)",
    ),
    "616": (
        "20210623051742",
        "Chickasaw-Nation-Announces-Election-Results-616",
        "Chickasaw Nation Announces Election Results (September 19, 2006)",
    ),
    "627": (
        "20191211101324",
        "Case-Wins-Chickasaw-Nation-Runoff-Election-627",
        "Case Wins Chickasaw Nation Runoff Election (December 13, 2006)",
    ),
    "673": (
        "20200814012315",
        "Chickasaw-Nation-Announces-Election-Results-673",
        "Chickasaw Nation Announces Election Results (July 31, 2007)",
    ),
    "727": (
        "20200807124908",
        "Chickasaw-Nation-announces-election-candidates-727",
        "Chickasaw Nation announces election candidates (June 4, 2008)",
    ),
    "1830": (
        "20220612225341",
        "Chickasaw-Nation-Announces-General-Election-Result-1830",
        "Chickasaw Nation Announces General Election Results (July 29, 2015)",
    ),
    "44938": (
        "20190905063248",
        "Chickasaw-Nation-Announces-General-Election-Result-44938",
        "Chickasaw Nation Announces General Election Results (July 26, 2017)",
    ),
    "51700": (
        "20190820050918",
        "Chickasaw-Nation-Announces-Election-Results-51700",
        "Chickasaw Nation Announces Election Results (July 30, 2019)",
    ),
    "51807": (
        "20190922161807",
        "Chickasaw-Nation-Announces-Runoff-Election-Results-51807",
        "Chickasaw Nation Announces Runoff Election Results (August 27, 2019)",
    ),
    "54354": (
        "20200803234233",
        "Chickasaw-Nation-Announces-General-Election-Result-54354",
        "Chickasaw Nation Announces General Election Results (July 28, 2020)",
    ),
    "56289": (
        "20210620014957",
        "Chickasaw-Nation-Announces-Candidates-For-General-56289",
        "Chickasaw Nation Announces Candidates for General Election (June 9, 2021)",
    ),
    "58855": (
        "20220803205942",
        "Chickasaw-Nation-Announces-Election-Results-58855",
        "Chickasaw Nation Announces Election Results (August 2, 2022)",
    ),
    "60785": (
        "20230818155400",
        "Chickasaw-Nation-Announces-Election-Results-60785",
        "Chickasaw Nation Announces Election Results (July 25, 2023)",
    ),
    "60938": (
        "20230831223633",
        "Chickasaw-Nation-Announces-Runoff-Election-results-60938",
        "Chickasaw Nation Announces Runoff Election Results (August 29, 2023)",
    ),
    "62371": (
        "20240606182120",
        "Chickasaw-Nation-Election-Secretary-Rita-Loder-ann-62371",
        "Chickasaw Nation Election Secretary Rita Loder announces candidates for general election (June 6, 2024)",
    ),
    "53974": (
        "20250606205119",
        "Chickasaw-Nation-Election-Secretary-Jerri-Martin-a-53974",
        "Chickasaw Nation Election Secretary Jerri Martin announces candidates for general election (June 6, 2025)",
    ),
    "56222": (
        "20260716204051",
        "Chickasaw-Nation-Election-Secretary-Jerri-Martin-a-56222",
        "Chickasaw Nation Election Secretary Jerri Martin announces candidates for general election (June 3, 2026)",
    ),
    "56795": (
        None,
        "Chickasaw-Nation-Election-Secretary-Announces-Spec-56795",
        "Chickasaw Nation Election Secretary Announces Special Election Results (August 25, 2026)",
    ),
}
HOSTS = {
    "1830": "https://www.",
    "54354": "https://www.",
    "60785": "https://www.",
    "53974": "http://",
    "56222": "https://www.",
}
POLITY = "tribal:chickasaw-nation"
NATION = "Chickasaw Nation"
UNOFFICIAL = {"56795"}
EXECUTIVE = {
    "Governor": ("governor", "Governor of the Chickasaw Nation", "head_of_state"),
    "Lieutenant Governor": ("lieutenant-governor", "Lieutenant Governor of the Chickasaw Nation", "other"),
}
PEOPLE = {
    "Mike Watson": "Michael T. Watson",
    "Michael Thomas Watson": "Michael T. Watson",
    "Lisa J. Billy": "Lisa Johnson Billy",
    "Mark Colbert": "Mark H. Colbert",
    "Mark Holmes Colbert": "Mark H. Colbert",
    "Steven E. Woods": "Steven Woods",
    "Cheri L. Bellefeuille-Gordon": "Cheri Bellefeuille-Gordon",
}
COUNTED = "Nation-wide totals as announced by the Chickasaw Nation Election Secretary in the nation's press release. Ballots are cast by mail; the release gives each candidate's votes but no ballots cast or registered voters."
TACIT = "No challenger filed for this seat. Under the Chickasaw Nation Code, a candidate who draws no opponent is elected by a one-vote margin, the act of filing being counted as an affirmative vote, so no ballots were counted."
PRELIMINARY = "Nation-wide totals as announced by the Chickasaw Nation Election Secretary on election night; the release calls them unofficial until the recount period ended on August 28, 2026."


def url(release: str) -> str:
    stamp, name, _ = RELEASES[release]
    page = f"{HOSTS.get(release, 'https://')}{SITE}/{name}.aspx"
    return page if stamp is None else f"https://web.archive.org/web/{stamp}id_/{page}"


def fold(text: str) -> str:
    return slug(PEOPLE.get(text, text)).replace("-", "")


def fetch() -> None:
    for release in RELEASES:
        raw.fetch(SOURCE, url(release), suffix=".html")


def rows() -> dict[str, dict[str, list[dict[str, str]]]]:
    out: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    with DATA.open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            out[f"{row['date']}:{row['kind']}"][row["contest"]].append(row)
    return out


def emit() -> Path:
    data = rows()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Chickasaw Nation elections 2006 to 2026 from the Election Secretary's announcements in the nation's press releases: Governor, Lieutenant Governor, the Chickasaw Nation Legislature by district seat, and the Supreme Court, nation-wide totals and uncontested seats",
            polity="US",
        )

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        nation = cs.key("us.polity", POLITY)
        if nation in cs.minted:
            cs.add("polity", id=nation, kind="tribal_nation", iso_code=None)
            name(nation, NATION)
            cs.add("polity_parent", polity_id=nation, parent_id=cs.polity("US"))
            cs.add("slug", path="us/chickasaw-nation", entity_id=nation)
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
            name(unit, "Chickasaw Nation (all ballots)")
        fptp = cs.key("us.electoral_system", "fptp")
        legislature = cs.key("us.body", "chickasaw-nation:legislature")
        if legislature in cs.minted:
            cs.add("body", id=legislature, polity_id=nation, kind="unicameral")
            name(legislature, "Chickasaw Nation Legislature")
        offices: dict[str, UUID] = {}

        def office(key: str, title: str, kind: str) -> UUID:
            if key not in offices:
                offices[key] = cs.key("us.office", f"chickasaw-nation:{key}")
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

        sources: dict[str, UUID] = {}

        def source(release: str) -> UUID:
            if release not in sources:
                title = RELEASES[release][2]
                sources[release] = cs.source(PUBLISHER, title)
                if sources[release] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[release],
                        kind="official_body",
                        publisher=PUBLISHER,
                        title=title,
                        url=url(release),
                        license="Public record",
                    )
                cs.artifact(raw.cached(SOURCE, url(release)), sources[release])
            return sources[release]

        made = {"contests": 0, "values": 0, "tacit": 0}
        for election_key, contests in sorted(data.items()):
            day_text, kind = election_key.split(":")
            month = len(day_text) == 7
            day = date.fromisoformat(f"{day_text}-01" if month else day_text)
            election = cs.key("us.election", f"{day_text}:{POLITY}:{kind}")
            cs.add(
                "election",
                id=election,
                polity_id=nation,
                kind={"general": "general", "runoff": "runoff", "special": "by_election"}[kind],
                date_from=day,
                date_to=date(day.year, 7, 31) if month else day,
                date_precision="month" if month else "day",
                status="held",
            )
            label = {"general": "general election", "runoff": "runoff election", "special": "special election"}[kind]
            name(election, f"{day.year} Chickasaw Nation {label}")
            path = f"us/chickasaw-nation/{day_text}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            for heading, entries in contests.items():
                release = entries[0]["release"]
                cited = source(release)
                tacit = entries[0]["votes"] == ""
                body = seat = None
                if heading in EXECUTIVE:
                    office_key, office_title, office_kind = EXECUTIVE[heading]
                    seat = office(office_key, office_title, office_kind)
                    contest_name = f"{day.year} {office_title} {label}"
                    contest_slug = office_key
                elif heading.startswith("Supreme Court "):
                    number = heading.removeprefix("Supreme Court ")
                    seat = office(
                        f"supreme-court-{number}",
                        f"Justice of the Chickasaw Nation Supreme Court, Seat {number}",
                        "other",
                    )
                    contest_name = f"{day.year} Chickasaw Nation Supreme Court, Seat {number} {label}"
                    contest_slug = f"supreme-court-{number}"
                else:
                    district, number = heading.split(" ")
                    body = legislature
                    contest_name = f"{day.year} Chickasaw Nation Legislature, {district} District Seat {number} {label}"
                    contest_slug = f"legislature-{slug(district)}-{number}"
                key = f"{day_text}:{POLITY}:{kind}:{contest_slug}"
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if body else "office",
                    office_id=seat,
                    body_id=body,
                    district_area_id=None,
                    term="remainder" if kind == "special" or (kind == "runoff" and heading == "Pontotoc 3") else "full",
                    magnitude=1,
                    round_number=2 if kind == "runoff" else 1,
                    electoral_system_id=fptp,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, contest_name)
                cs.add("slug", path=f"{path}/{contest_slug}", entity_id=contest)
                cs.add(
                    "citation",
                    entity_id=contest,
                    source_id=cited,
                )
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="preliminary" if release in UNOFFICIAL else "official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=cited,
                    is_default=True,
                    completeness=None if tacit else 1,
                )
                note = TACIT if tacit else PRELIMINARY if release in UNOFFICIAL else COUNTED
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                ranked = sorted(entries, key=lambda row: -int(row["votes"] or 0))
                if not tacit and len(ranked) > 1 and ranked[0]["votes"] == ranked[1]["votes"]:
                    raise ValueError(f"{key}: tie at the top")
                if not tacit and ranked[0]["outcome"] == "":
                    raise ValueError(f"{key}: the leader has no outcome")
                for position, row in enumerate(entries, start=1):
                    option = cs.key("us.option", f"{key}:{fold(row['label'])}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        ballot_label=row["label"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{fold(row['label'])}:head"),
                        person_id=person(row["label"]),
                        option_id=option,
                        role="head",
                        ballot_name=row["label"],
                        is_independent=True,
                    )
                    if not tacit:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=int(row["votes"]),
                        )
                        made["values"] += 1
                    if row["outcome"]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind=row["outcome"],
                            seats=1 if row["outcome"] == "elected" else None,
                        )
                made["contests"] += 1
                made["tacit"] += tacit
                print(
                    f"{day_text} {kind} {contest_slug}: "
                    + ", ".join(f"{r['label']} {r['votes'] or '-'}" for r in entries)
                )
        print(made)
        return cs.write()
