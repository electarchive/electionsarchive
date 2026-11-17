"""Mongolia's General Election Commission: presidential and parliamentary elections by polling section."""

import functools
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.mn import local, parliament, places, polls, sheets, translit

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
KHURAL = key("body", "state-great-khural")


def council_body(council: local.Council) -> str:
    return key("body", "council", council.key)


BLOCK_2024 = key("electoral_system", "block", 2024)
BLOCK_METHOD = (
    "In each multi-member constituency a voter marks up to as many candidates as the constituency has seats, and "
    "the candidates with the most votes win the seats. Votes are stored per candidate, as the Commission counted "
    "them for each polling section."
)
LIST_METHOD = (
    "A separate nationwide ballot elects 48 members by party list. Votes are stored per party and polling section, "
    "with the votes cast abroad as one unit."
)
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

PARTIES: dict[str, tuple[str, str | None]] = {
    "МАН": ("Монгол Ардын Нам", "Mongolian People's Party"),
    "АН": ("Ардчилсан Нам", "Democratic Party"),
    "МАХН": ("Монгол Ардын Хувьсгалт Нам", "Mongolian People's Revolutionary Party"),
    "ЗХЭЭ": ("Зөв Хүн Электорат Эвсэл", "Right Person Electorate Coalition"),
    "ҮЭ": ("ҮНДЭСНИЙ ЭВСЭЛ", None),
    "ИЗНН": ("Иргэний зориг ногоон нам", None),
    "МУНН-ШН": ('"ШИНЭ НЭГДСЭН ЭВСЭЛ" (МУНН-ШН)', None),
    "МоЛАН": ("Монголын Либерал Ардчилсан нам", None),
    "ЭОН": ("Эх Орон нам", None),
    "БНН": ("Бүгд найрамдах нам", None),
    "МСДН": ("Монголын Социал Демократ Нам", None),
    "ЭЧХН": ("Эрхчөлөөг хэрэгжүүлэгч нам", None),
    "ИХН": ("Иргэний хөдөлгөөний нам", None),
    "ХҮН": ("ХҮН нам", None),
    "ЭОНН": ("Эх орончдын нэгдсэн нам", None),
    "МКН": ("Монголконсерватив нам", None),
    "АТХН": ("Ард түмний хүч нам", None),
    "МХТН": ("Монголын хүний төлөө нам", None),
    "ҮБЗН": ("Үнэн ба Зөв нам", None),
    "ЭЧЭН": ("Эрх Чөлөөний Эвсэл нам", None),
    "АТОЗН": ("Ард түмний олонхийн засаглал нам", None),
    "ИОНН": ("Иргэдийн Оролцооны Нэгдэл Нам", None),
    "САИНН": ("Сайн ардчилсан иргэдийн нэгдсэн нам", None),
}
BY_NAME = {name.casefold(): abbreviation for abbreviation, (name, _) in PARTIES.items()}


def party(abbreviation: str | None) -> str | None:
    return key("party", abbreviation) if abbreviation in PARTIES else None


def president_polls() -> list[polls.Poll]:
    return [polls.president_2013(), polls.president_2017(), polls.president_2017_runoff(), polls.president_2021()]


@functools.cache
def all_polls() -> list[polls.Poll]:
    return president_polls()


@functools.cache
def parliament_2024() -> list[parliament.Contest]:
    contests = [parliament.constituency_2024(number) for number in parliament.CONSTITUENCIES_2024]
    sections = {row.code: row for contest in contests for row in contest.rows}
    return [*contests, parliament.list_2024(sections)]


def parliament_keys() -> list[str]:
    return [str(parliament.DAY_2024)]


PLACE_LIST_2020 = "1fa2007c"


@dataclass(frozen=True)
class LocalElection:
    key: str
    date_from: date
    date_to: date
    precision: str
    original_text: str | None
    english: str
    native: str
    blocks: list[local.Block]
    files: list[str]
    note: str | None = None


NATIVE_LOCAL = "Аймаг, нийслэл, сум, дүүргийн Иргэдийн Төлөөлөгчдийн Хурлын {year} оны ээлжит сонгууль"


@functools.cache
def local_elections() -> list[LocalElection]:
    locate = local.section_places(PLACE_LIST_2020)
    results_2020 = "cacc51cc"
    results_2024 = "1c20b694"
    blocks_2024 = local.blocks(results_2024, locate, "Аймаг 10-11") + local.blocks(results_2024, locate, "Сум 10-11")
    return [
        LocalElection(
            "2020-10-15",
            date(2020, 10, 15),
            date(2020, 10, 15),
            "day",
            None,
            "2020 Mongolian local council elections",
            NATIVE_LOCAL.format(year=2020),
            local.blocks(results_2020, locate),
            [sheets.url(results_2020), sheets.url(PLACE_LIST_2020)],
        ),
        LocalElection(
            "2024-10",
            date(2024, 10, 1),
            date(2024, 10, 31),
            "month",
            "The results workbook's tabs are dated 10-11; its supplementary tabs are dated 10-13 and 10-19.",
            "2024 Mongolian local council elections",
            NATIVE_LOCAL.format(year=2024),
            blocks_2024,
            [sheets.url(results_2024), sheets.url(PLACE_LIST_2020)],
        ),
    ]


def council_name(council: local.Council) -> tuple[str, str | None]:
    """The Mongolian and, where it is known, the English name of a council."""
    if council.kind == "capital":
        return "Нийслэлийн Иргэдийн Төлөөлөгчдийн Хурал", "Ulaanbaatar Citizens' Representatives Khural"
    if council.kind == "aimag":
        english = places.AIMAGS[places.aimag_code(council.aimag or "") or ""][1]
        return (
            f"{council.aimag} аймгийн Иргэдийн Төлөөлөгчдийн Хурал",
            f"{english} Aimag Citizens' Representatives Khural",
        )
    noun = "сумын" if council.kind == "soum" else "дүүргийн"
    place = f"{council.aimag} аймгийн " if council.aimag else ""
    return f"{place}{council.soum} {noun} Иргэдийн Төлөөлөгчдийн Хурал", None


def council_slug(council: local.Council) -> str:
    if council.kind == "capital":
        return "capital"
    if council.kind == "aimag":
        return changeset.slug(places.AIMAGS[places.aimag_code(council.aimag or "") or ""][1]) + "-aimag"
    place = council.place
    english = places.AIMAGS[place.aimag][1] if place else ""
    return changeset.slug(f"{english} {translit.latin(council.soum or '')} {council.kind}")


def council_area(council: local.Council) -> str:
    if council.kind == "capital":
        return area("mn.aimag", places.CAPITAL)
    if council.kind == "aimag":
        return area("mn.aimag", places.aimag_code(council.aimag or "") or "")
    place = council.place
    assert place is not None
    return area("mn.soum", place.code)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{poll.key}" for poll in all_polls()),
        *(f"{SOURCE}/{day}" for day in parliament_keys()),
        *(f"{SOURCE}/{election.key}" for election in local_elections()),
    ]


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
    legislature_2024().write()
    for election_ in local_elections():
        councils(election_).write()


def emit() -> None:
    package.run("load", packages())


def _places() -> dict[str, places.Place]:
    found: dict[str, places.Place] = {}
    rows = [row for poll in all_polls() for row in poll.rows]
    rows += [row for contest in parliament_2024() for row in contest.rows]
    blocks = [block for election_ in local_elections() for block in election_.blocks]
    rows += [row for block in blocks for row in block.rows]
    for row in rows:
        if row.place is not None:
            found.setdefault(row.place.code, row.place)
    for block in blocks:
        if block.council.place is not None:
            found.setdefault(block.council.place.code, block.council.place)
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
        ("mn.constituency", 2, "electoral_district", "parliamentary constituency"),
        ("mn.council_constituency", 2, "electoral_district", "council constituency"),
    ):
        p.add("scheme", code=code, polity_id=MONGOLIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, (name, english) in places.AIMAGS.items():
        id = area("mn.aimag", code)
        p.add("area", id=id, scheme_code="mn.aimag")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, english, "en", "official")
        p.name(id, name, "mn", "native", False)
    for contest in parliament_2024():
        if contest.number is None:
            continue
        id = area("mn.constituency", f"{parliament.DAY_2024.year}-{contest.number}")
        p.add("area", id=id, scheme_code="mn.constituency")
        p.add("area_code", area_id=id, code=f"{parliament.DAY_2024.year}-{contest.number}")
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, f"{parliament.DAY_2024.year} constituency {contest.number}", "en", "official")
        p.name(id, f"{contest.number}-р тойрог", "mn", "native", False)
    for election_ in local_elections():
        for block in election_.blocks:
            code = f"{election_.key}:{block.council.key}:{block.number}"
            id = area("mn.council_constituency", code)
            p.add("area", id=id, scheme_code="mn.council_constituency")
            p.add("area_code", area_id=id, code=code)
            p.add("area_parent", area_id=id, parent_id=council_area(block.council))
            p.name(id, f"{council_name(block.council)[0]}, {block.number}-р тойрог", "mn", "native")
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
    files = {url for poll in all_polls() for url in poll.files}
    files |= {url for contest in parliament_2024() for url in contest.files}
    files |= {url for election_ in local_elections() for url in election_.files}
    for url in sorted(files):
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
    p.add("body", id=KHURAL, polity_id=MONGOLIA, kind="unicameral")
    p.name(KHURAL, "State Great Khural", "en", "official")
    p.name(KHURAL, "Улсын Их Хурал", "mn", "native", False)
    p.add(
        "electoral_system",
        id=BLOCK_2024,
        polity_id=MONGOLIA,
        valid_from=date(2023, 1, 1),
        valid_to=None,
        rules={"formula": "block"},
        rules_version=1,
    )
    p.add("note", id=key("note", BLOCK_2024), entity_id=BLOCK_2024, kind="methodology", body_md=BLOCK_METHOD, lang="en")
    seen: set[str] = set()
    for block in [block for election_ in local_elections() for block in election_.blocks]:
        if block.council.key in seen:
            continue
        seen.add(block.council.key)
        id = council_body(block.council)
        p.add("body", id=id, polity_id=MONGOLIA, kind="council")
        native, english = council_name(block.council)
        p.name(id, native, "mn", "native", english is None)
        if english:
            p.name(id, english, "en", "official")
    for abbreviation, (name, english) in PARTIES.items():
        id = party(abbreviation)
        assert id is not None
        p.add("party", id=id, kind="bloc" if "ЭВСЭЛ" in name.upper() else "party", polity_id=MONGOLIA)
        p.add("external_id", entity_id=id, scheme="mn.gec.party", value=abbreviation)
        p.name(id, name, "mn", "official")
        if english:
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


def legislature_2024() -> Package:
    day = parliament.DAY_2024
    contests = parliament_2024()
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The 2024 Mongolian parliamentary election, the 13 constituencies and the nationwide party list, by "
        "polling section from the General Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MONGOLIA, TERRITORY}
    for url in sorted({url for contest in contests for url in contest.files}):
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", str(day))
    p.add("election", id=election_id, polity_id=MONGOLIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, "2024 Mongolian parliamentary election", "en", "official")
    p.name(election_id, "Улсын Их Хурлын 2024 оны ээлжит сонгууль", "mn", "native", False)
    path = f"mn/{day}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units: dict[str, str] = {}
    for contest in contests:
        for row in contest.rows:
            if row.code in units:
                continue
            unit = units[row.code] = key("unit", str(day), row.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MONGOLIA,
                scheme_code="mn.hesg",
                code=f"{day}:{row.code}",
                area_relation="within" if row.place else "unlocated",
                area_id=area("mn.soum", row.place.code) if row.place else None,
                kind="standard" if row.place else "abroad_mission",
            )
            p.name(unit, row.label, "mn", "official")
    for contest in contests:
        tag = "list" if contest.number is None else str(contest.number)
        contest_id = key("contest", str(day), tag)
        english = (
            f"{day.year} Mongolian parliamentary election, party list"
            if contest.number is None
            else f"{day.year} Mongolian parliamentary election, constituency {contest.number}"
        )
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation" if contest.number is None else "body_seats",
            body_id=KHURAL,
            district_area_id=TERRITORY
            if contest.number is None
            else area("mn.constituency", f"{day.year}-{contest.number}"),
            magnitude=contest.seats,
            round_number=1,
            electoral_system_id=None if contest.number is None else BLOCK_2024,
            default_result_kind="counted",
        )
        p.name(contest_id, english, "en", "official")
        p.add(
            "slug",
            path=f"{path}/list" if contest.number is None else f"{path}/constituency-{contest.number}",
            entity_id=contest_id,
            is_current=True,
        )
        result_set = key("result_set", str(day), tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
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
            body_md=LIST_METHOD if contest.number is None else BLOCK_METHOD,
            lang="en",
        )
        options: list[str] = []
        for index, entry in enumerate(contest.candidates):
            option = key("option", str(day), tag, index)
            options.append(option)
            if contest.number is None:
                abbreviation = BY_NAME[entry.label.casefold()]
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="list",
                    party_id=party(abbreviation),
                    ballot_position=index + 1,
                    ballot_label=entry.label,
                )
            else:
                person = key("person", str(day), tag, index)
                p.add("person", id=person)
                p.name(person, entry.label, "mn", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="candidate",
                    party_id=party(entry.party),
                    ballot_position=index + 1,
                    ballot_label=entry.label + (f" ({entry.party})" if entry.party else ""),
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", str(day), tag, index),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=entry.label,
                    member_of_party_id=party(entry.party),
                    is_independent=entry.party is None,
                )
            if contest.declared is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=contest.declared[index])
        for row in contest.rows:
            for option, value in zip(options, row.votes, strict=True):
                if value is not None:
                    _value(p, result_set, units[row.code], option, 1, value)
    return p


def councils(spec: LocalElection) -> Package:
    day = spec.key
    blocks = spec.blocks
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {spec.english} (capital, aimag, district, and soum councils) by polling section and constituency "
        "from the General Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MONGOLIA, TERRITORY}
    for url in spec.files:
        p.uses(raw.cached(SOURCE, url))
    election_id = key("election", str(day))
    p.add(
        "election",
        id=election_id,
        polity_id=MONGOLIA,
        kind="local",
        date_from=spec.date_from,
        date_to=spec.date_to,
        date_precision=spec.precision,
        original_text=spec.original_text,
        status="held",
    )
    p.name(election_id, spec.english, "en", "official")
    p.name(election_id, spec.native, "mn", "native", False)
    path = f"mn/{day}-local"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units: dict[str, str] = {}
    for block in blocks:
        for row in block.rows:
            if row.code in units:
                continue
            unit = units[row.code] = key("unit", str(day), row.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MONGOLIA,
                scheme_code="mn.hesg",
                code=f"{day}:{row.code}",
                area_relation="within" if row.place else "unlocated",
                area_id=area("mn.soum", row.place.code) if row.place else None,
                kind="standard",
            )
            p.name(unit, f"{row.code}", "mn", "official")
    for block in blocks:
        tag = f"{block.council.key}:{block.number}"
        contest = key("contest", str(day), tag)
        native, _ = council_name(block.council)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=council_body(block.council),
            district_area_id=area("mn.council_constituency", f"{day}:{tag}"),
            magnitude=block.seats,
            round_number=1,
            electoral_system_id=BLOCK_2024,
            default_result_kind="counted",
        )
        p.name(contest, f"{native}, {block.number}-р тойрог", "mn", "native")
        p.add("slug", path=f"{path}/{council_slug(block.council)}-{block.number}", entity_id=contest, is_current=True)
        result_set = key("result_set", str(day), tag)
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
            body_md=BLOCK_METHOD,
            lang="en",
        )
        options: list[str] = []
        for index, entry in enumerate(block.candidates):
            option = key("option", str(day), tag, index)
            options.append(option)
            person = key("person", str(day), tag, index)
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
                id=key("candidacy", str(day), tag, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=entry.label,
                member_of_party_id=party(entry.party),
                is_independent=entry.party is None,
            )
            if block.declared is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=block.declared[index])
        for row in block.rows:
            for option, value in zip(options, row.votes, strict=True):
                if value is not None:
                    _value(p, result_set, units[row.code], option, 1, value)
    return p
