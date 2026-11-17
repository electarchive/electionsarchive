"""Mongolia's General Election Commission: the presidential elections by polling section."""

import functools
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.mn import places, polls

SOURCE = "mn.gec"
MONGOLIA = str(uuid5(package.NAMESPACE, "polity\x1fMN"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMNG"))
PORTAL = "https://m-election.mn/electionresult"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


GEC_SOURCE = key("source", "gec")
PRESIDENCY = key("office", "president")
PRESIDENT_SYSTEM = key("electoral_system", "president", 1993)
PRESIDENT_METHOD = (
    "The President is elected directly for one term of six years. A candidate who wins more than half of the valid "
    "ballots is elected in the first round; otherwise the two leading candidates go to a second round. The "
    "Commission publishes the count for each polling section (hesgiin komiss), with the section's invalid and blank "
    "ballots where it prints them. Votes are stored as the Commission published them, and each national total is the "
    "Commission's own."
)
PRESIDENT_NAMES = {
    date(2013, 6, 26): ("2013 Mongolian presidential election", "Монгол Улсын Ерөнхийлөгчийн 2013 оны сонгууль"),
    date(2017, 6, 26): ("2017 Mongolian presidential election", "Монгол Улсын Ерөнхийлөгчийн 2017 оны сонгууль"),
    date(2017, 7, 7): (
        "2017 Mongolian presidential election, second round",
        "Монгол Улсын Ерөнхийлөгчийн 2017 оны сонгууль, хоёр дахь тойрог",
    ),
    date(2021, 6, 9): ("2021 Mongolian presidential election", "Монгол Улсын Ерөнхийлөгчийн 2021 оны сонгууль"),
}

PARTIES: dict[str, tuple[str, str]] = {
    "МАН": ("Монгол Ардын Нам", "Mongolian People's Party"),
    "АН": ("Ардчилсан Нам", "Democratic Party"),
    "МАХН": ("Монгол Ардын Хувьсгалт Нам", "Mongolian People's Revolutionary Party"),
    "ЗХЭЭ": ("Зөв Хүн Электорат Эвсэл", "Right Person Electorate Coalition"),
}


def party(abbreviation: str | None) -> str | None:
    return key("party", abbreviation) if abbreviation in PARTIES else None


def president_polls() -> list[polls.Poll]:
    return [polls.president_2013(), polls.president_2017(), polls.president_2017_runoff(), polls.president_2021()]


@functools.cache
def all_polls() -> list[polls.Poll]:
    return president_polls()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{poll.key}" for poll in all_polls())]


def fetch() -> None:
    # The Commission's site serves its workbooks to a browser only, so they are supplied to the cache with
    # raw.store under the keys in the raw index; fetching has nothing more to ask for.
    for url in raw.keys(SOURCE):
        if url.startswith("http"):
            print(f"{url}: cached", flush=True)


def parse() -> None:
    geo().write()
    registry().write()
    for poll in all_polls():
        election(poll).write()


def emit() -> None:
    package.run("load", packages())


def _places() -> dict[str, places.Place]:
    found: dict[str, places.Place] = {}
    for poll in all_polls():
        for row in poll.rows:
            if row.place is not None:
                found.setdefault(row.place.code, row.place)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Mongolia's 21 provinces and the capital, the soums and districts, and the polling-section scheme",
    )
    p.external |= {MONGOLIA, TERRITORY}
    for code, rank, kind, noun in (
        ("mn.aimag", 1, "administrative", "province or capital"),
        ("mn.soum", 2, "administrative", "soum or district"),
        ("mn.hesg", 3, "precinct", "polling section"),
    ):
        p.add("scheme", code=code, polity_id=MONGOLIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, (name, english) in places.AIMAGS.items():
        id = area("mn.aimag", code)
        p.add("area", id=id, scheme_code="mn.aimag")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, english, "en", "official")
        p.name(id, name, "mn", "native", False)
    for code, place in sorted(_places().items()):
        id = area("mn.soum", code)
        p.add("area", id=id, scheme_code="mn.soum")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("mn.aimag", place.aimag))
        p.name(id, place.soum, "mn", "native")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Mongolian parties, the presidency, and its electoral system, from the General Election Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {MONGOLIA, TERRITORY}
    p.add(
        "source",
        id=GEC_SOURCE,
        kind="official_body",
        publisher="Улсын Сонгуулийн Ерөнхий Хороо",
        title="Election results of the General Election Commission of Mongolia",
        url=PORTAL,
        attribution="General Election Commission of Mongolia",
    )
    for url in sorted({url for poll in all_polls() for url in poll.files}):
        p.artifact(raw.cached(SOURCE, url), GEC_SOURCE)
    p.add("office", id=PRESIDENCY, polity_id=MONGOLIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Mongolia", "en", "official")
    p.name(PRESIDENCY, "Монгол Улсын Ерөнхийлөгч", "mn", "native", False)
    p.add(
        "electoral_system",
        id=PRESIDENT_SYSTEM,
        polity_id=MONGOLIA,
        valid_from=date(1993, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", PRESIDENT_SYSTEM),
        entity_id=PRESIDENT_SYSTEM,
        kind="methodology",
        body_md=PRESIDENT_METHOD,
        lang="en",
    )
    for abbreviation, (name, english) in PARTIES.items():
        id = party(abbreviation)
        assert id is not None
        p.add("party", id=id, kind="party", polity_id=MONGOLIA)
        p.add("external_id", entity_id=id, scheme="mn.gec.party", value=abbreviation)
        p.name(id, name, "mn", "official")
        p.name(id, english, "en", "official", False)
        p.name(id, abbreviation, "mn", "abbreviation", False)
    return p


def election(poll: polls.Poll) -> Package:
    p = Package(
        f"{SOURCE}/{poll.key}",
        SOURCE,
        f"The {poll.day.year} Mongolian presidential election"
        + ("" if poll.first_round else ", second round")
        + " by polling section from the General Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MONGOLIA, TERRITORY}
    for url in poll.files:
        p.uses(raw.cached(SOURCE, url))
    english, native = PRESIDENT_NAMES[poll.day]
    election_id = key("election", poll.key)
    p.add(
        "election",
        id=election_id,
        polity_id=MONGOLIA,
        kind="general" if poll.first_round else "runoff",
        date_from=poll.day,
        date_to=poll.day,
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, native, "mn", "native", False)
    path = f"mn/{poll.day}-presidential" + ("" if poll.first_round else "-runoff")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", poll.key)
    previous = None
    if not poll.first_round:
        previous = key("contest", "2017-06-26")
        p.external.add(previous)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=1 if poll.first_round else 2,
        previous_round_id=previous,
        electoral_system_id=PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", poll.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=GEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=PRESIDENT_METHOD,
        lang="en",
    )
    leader = max(range(len(poll.candidates)), key=lambda i: poll.declared[i])
    won = not poll.first_round or poll.declared[leader] * 2 > sum(poll.declared) + poll.declared_extra.get(
        polls.BLANK, 0
    )
    top = sorted(range(len(poll.candidates)), key=lambda i: -poll.declared[i])[:2]
    options: list[str] = []
    for index, entry in enumerate(poll.candidates):
        option = key("option", poll.key, index)
        options.append(option)
        person = key("person", poll.key, index)
        p.add("person", id=person)
        p.name(person, entry.label, "mn", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(entry.party),
            ballot_position=index + 1,
            ballot_label=entry.label + (f" ({entry.party})" if entry.party else ""),
        )
        p.add(
            "candidacy",
            id=key("candidacy", poll.key, index),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=entry.label,
            member_of_party_id=party(entry.party),
            is_independent=False,
        )
        if won and index == leader:
            outcome = "elected"
        elif not poll.first_round:
            outcome = "lost"
        else:
            outcome = "advanced" if not won and index in top else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=poll.declared[index])
    for measure, value in poll.declared_extra.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, row in enumerate(poll.rows):
        unit = key("unit", poll.key, row.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MONGOLIA,
            scheme_code="mn.hesg",
            code=f"{poll.key}:{row.code}",
            area_relation="within" if row.place else "unlocated",
            area_id=area("mn.soum", row.place.code) if row.place else None,
            kind="standard" if row.place else "abroad_mission",
        )
        p.name(unit, row.label, "mn", "official")
        for option, value in zip(options, row.votes, strict=True):
            if value is not None:
                _value(p, result_set, unit, option, 1, value)
        for measure, value in row.extra.items():
            _value(p, result_set, unit, None, measure, value)
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
