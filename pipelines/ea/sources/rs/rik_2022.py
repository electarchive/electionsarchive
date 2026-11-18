"""Serbia's parliamentary and presidential elections of 3 April 2022, national results from the Commission's reports.

For these two elections the Republic Electoral Commission published the minutes of every polling board (form RG-2) as
scanned PDFs only, and no table of results by polling station or by municipality. What it printed as text are the two
reports of overall results, adopted at its 70th session (president, 9 May 2022) and its 79th (National Assembly,
5 July 2022), after the votes repeated at some polling stations were counted. Each report gives the number of polling
stations, the registered voters, the voters who came out, the ballots cast, the invalid and valid ballots, and each
list's or candidate's votes, with the seats of each list. The packages hold those national figures.
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik

DAY = date(2022, 4, 3)
PARLIAMENT, PRESIDENT = "2022-04-03", "2022-04-03-president"
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fSRB"))
REGISTERED, VOTERS, CAST, INVALID, VALID, VOTES = (
    rik.REGISTERED,
    rik.VOTERS,
    rik.CAST,
    rik.INVALID,
    rik.VALID,
    rik.VOTES,
)
URLS = {
    PARLIAMENT: f"{rik.BASE}/vest/324925/79-sednica-republicke-izborne-komisije.php",
    PRESIDENT: f"{rik.BASE}/vest/317359/70-sednica-republicke-izborne-komisije.php",
}
METHOD = (
    "National results as the Republic Electoral Commission printed them in its report of overall results, which "
    "sums the reports of the local electoral commissions and counts the votes repeated at some polling stations. The "
    "Commission published the polling boards' minutes for this election only as scanned forms, so no figure is "
    "stored for a polling station or a municipality. Registered voters are the voter register, voters are those who "
    "came out to vote, ballots cast are the ballots in the boxes, and valid votes are the valid ballots."
)

# the nominators and names of the eight candidates, in ballot order
CANDIDATES = [
    ("МИША ВАЦИЋ", "Група грађана „Српски патриота – Миша Вацић“"),
    ("ПРОФ. ДР БИЉАНА СТОЈКОВИЋ", "Група грађана МОРАМО – БИЉАНА СТОЈКОВИЋ, председница Србије"),
    (
        "БРАНКА СТАМЕНКОВИЋ",
        "Коалиција СУВЕРЕНИСТИ - Саша Радуловић (ДЈБ), Милан Стаматовић (ЗС), др Јована Стојковић (ЖЗС)",
    ),
    ("ЗДРАВКО ПОНОШ", "ГРУПА ГРАЂАНА ЗА УЈЕДИЊЕНУ, ПРАВЕДНУ И СТАБИЛНУ СРБИЈУ"),
    ("МИЛИЦА ЂУРЂЕВИЋ СТАМЕНКОВСКИ", "Српска странка Заветници"),
    ("АЛЕКСАНДАР ВУЧИЋ", "Коалиција Александар Вучић – Заједно можемо све"),
    ("ДР МИЛОШ ЈОВАНОВИЋ", "КОАЛИЦИЈА ЗА КРАЉЕВИНУ СРБИЈУ – ДСС"),
    ("БОШКО ОБРАДОВИЋ", "Коалиција: Српски покрет ДВЕРИ - ПОКС - Патриотски блок за обнову Краљевине Србије"),
]
WINNER = 6


@dataclass
class Report:
    declared: dict[int, int]
    votes: dict[int, int]
    seats: dict[int, int]
    labels: dict[int, str]
    stations: int


def key(kind: str, *parts: object) -> str:
    return rik.key(kind, *parts)


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(rik.SOURCE, url, suffix=".html")


def _text(slug: str) -> str:
    path = raw.cached(rik.SOURCE, URLS[slug]).path
    page = path.read_text(encoding="utf-8")
    start = page.index("</h1>")
    body = re.sub(r"<script.*?</script>", " ", page[start:], flags=re.DOTALL)
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
    return text[: text.index("Поделите овај текст")]


def _num(text: str) -> int:
    return int(text.replace(".", ""))


def _figures(text: str) -> tuple[dict[int, int], int]:
    def read(label: str) -> int:
        found = re.search(re.escape(label) + r"\s+(\d{1,3}(?:\.\d{3})*)", text)
        if found is None:
            raise ValueError(f"the report has no figure for {label!r}")
        return _num(found.group(1))

    declared = {
        REGISTERED: read("Број бирача уписаних у бирачки списак"),
        VOTERS: read("Број бирача који је изашао на изборе"),
        CAST: read("Број бирача који су гласали"),
        INVALID: read("Број неважећих гласачких листића"),
        VALID: read("Број важећих гласачких листића"),
    }
    return declared, read("Број бирачких места на којима је обављено гласање")


def report(slug: str) -> Report:
    text = _text(slug)
    president = slug == PRESIDENT
    declared, stations = _figures(text)
    votes: dict[int, int] = {}
    seats: dict[int, int] = {}
    labels: dict[int, str] = {}
    if president:
        for number, (name, nominator) in enumerate(CANDIDATES, 1):
            found = re.search(
                rf"(?<!\d){number}\. {re.escape(name)}\s.*?"
                r"(\d{1,3}(?:\.\d{3})*) \d+,\d+%",
                text,
            )
            if found is None:
                raise ValueError(f"the report has no row for candidate {number} {name}")
            votes[number] = _num(found.group(1))
            labels[number] = name
        if "Александар Вучић" not in text.split("изабран")[-1]:
            raise ValueError("the report does not declare Aleksandar Vučić elected")
    else:
        rows = re.finditer(
            r"(?<!\d)(\d{1,2})\. (.+?) (\d{1,3}(?:\.\d{3})*) (\d{1,3})(?= \d{1,2}\. |\s+На основу)",
            text[text.index("Број гласова и мандата") :],
        )
        for row in rows:
            number = int(row.group(1))
            if number != len(votes) + 1:
                raise ValueError(f"list {number} is out of order")
            labels[number] = row.group(2)
            votes[number] = _num(row.group(3))
            seats[number] = int(row.group(4))
        if len(votes) != 19:
            raise ValueError(f"read {len(votes)} lists from the report, expected 19")
        if sum(seats.values()) != 250:
            raise ValueError(f"the seats add up to {sum(seats.values())}, not 250")
    if sum(votes.values()) != declared[VALID]:
        raise ValueError(f"{slug}: the votes add up to {sum(votes.values()):,}, not {declared[VALID]:,}")
    if declared[INVALID] + declared[VALID] != declared[CAST]:
        raise ValueError(f"{slug}: valid and invalid ballots do not add up to the ballots cast")
    return Report(declared, votes, seats, labels, stations)


def packages() -> list[str]:
    return [f"{rik.SOURCE}/{PARLIAMENT}", f"{rik.SOURCE}/{PRESIDENT}"]


def parse() -> None:
    for slug in (PARLIAMENT, PRESIDENT):
        build(slug).write()


def build(slug: str) -> Package:
    president = slug == PRESIDENT
    r = report(slug)
    title = "2022 Serbian presidential election" if president else "2022 Serbian parliamentary election"
    native = (
        "Избори за председника Републике Србије 2022."
        if president
        else "Избори за народне посланике Народне скупштине 2022."
    )
    p = Package(
        f"{rik.SOURCE}/{slug}",
        rik.SOURCE,
        f"The 2022 Serbian {'presidential' if president else 'parliamentary'} election, national results, from the "
        "Republic Electoral Commission",
        depends=[f"{rik.SOURCE}/geo", f"{rik.SOURCE}/registry"],
    )
    p.external |= {rik.SERBIA, COUNTRY}
    p.artifact(raw.cached(rik.SOURCE, URLS[slug]), rik.SOURCE_ID)
    election = key("election", slug)
    path = f"rs/{DAY}-{'presidential' if president else 'general'}"
    p.add("election", id=election, polity_id=rik.SERBIA, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(election, title, "en", "official")
    p.name(election, native, "sr", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", slug)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if president else "list_allocation",
        office_id=rik.PRESIDENCY if president else None,
        body_id=None if president else rik.BODIES["parliament"],
        magnitude=1 if president else 250,
        round_number=1,
        previous_round_id=None,
        electoral_system_id=rik.SYSTEM_OF["president" if president else "parliament"],
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    p.add("slug", path=f"{path}/{'president' if president else 'assembly'}", entity_id=contest, is_current=True)
    result_set = key("result_set", slug)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=rik.SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD + (
        " The Commission declared Aleksandar Vučić elected."
        if president
        else " Seats are as the Commission declared them for each list; a list with no seats has no outcome."
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: dict[int, str] = {}
    for number, label in sorted(r.labels.items()):
        option = options[number] = key("option", slug, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if president else "list",
            ballot_position=number,
            ballot_label=label,
            party_id=None if president else rik.party(slug, number),
        )
        if president:
            person = key("person", slug, number)
            p.add("person", id=person, birth_precision=None)
            p.name(person, label.removeprefix("ПРОФ. ").removeprefix("ДР "), "sr", "official")
            p.add(
                "candidacy",
                id=key("candidacy", slug, number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
            )
            p.add(
                "note",
                id=key("note", key("candidacy", slug, number)),
                entity_id=key("candidacy", slug, number),
                kind="context",
                body_md=f"Предлагач: {CANDIDATES[number - 1][1]}",
                lang="sr",
            )
    if president:
        p.add("outcome", result_set_id=result_set, option_id=options[WINNER], kind="won")
    for number, seats in r.seats.items():
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=options[number], kind="won", seats=seats)
    for measure, value in r.declared.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for number, value in r.votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[number], measure=VOTES, value=value)
    unit = key("unit", f"{DAY}:{slug}:total")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=rik.SERBIA,
        scheme_code="rs.opstina",
        code=f"{slug}:total",
        area_relation="within",
        area_id=COUNTRY,
        kind="aggregate",
    )
    p.name(unit, "Република Србија", "sr", "official")
    rows = [(unit, None, m, v) for m, v in r.declared.items()]
    rows += [(unit, options[n], VOTES, v) for n, v in r.votes.items()]
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": [result_set] * len(rows),
                "unit_id": [x[0] for x in rows],
                "option_id": [x[1] for x in rows],
                "channel": [0] * len(rows),
                "measure": [x[2] for x in rows],
                "count_round": [0] * len(rows),
                "flags": [0] * len(rows),
                "value": [x[3] for x in rows],
            },
            schema_overrides={"option_id": pl.String},
        ),
    )
    return p
