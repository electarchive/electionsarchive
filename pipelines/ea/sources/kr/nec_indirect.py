"""South Korea's seven indirect presidential elections, 1948 to 1981.

Seven presidents were chosen without a popular vote for the office: by the Constituent National Assembly on
20 July 1948 and by the National Assembly's two houses on 12 August 1960, by the National Conference for Unification
on 23 December 1972, 6 July 1978, 6 December 1979, and 27 August 1980, and by a college of presidential electors on
25 February 1981. The National Election Commission's statistics system prints each vote in its counting report
(개표현황, VCCP09, old election type 0): the electors on the roll, the ballots cast, the votes for each person, the
invalid ballots, and the abstentions. The winners list (당선인명부, EPEI01) names the winner and the winner's votes
again, and is checked against the counting report. The votes were taken in a chamber or a hall, so there is no breakdown
by sido or sigungu; the votes are stored on the national unit.

The 1960 table is printed in a pop-up that every counting report carries; the report of its own day is empty.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlencode
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.kr.nec import BASE, LICENSE, PUBLISHER, REPORT, _get, _winner_form, _winner_key

SOURCE = "kr.nec_indirect"
KOREA = str(uuid5(package.NAMESPACE, "polity\x1fKR"))
# Rows production already holds: the national area (kr.nec), the national reporting unit (kr.referendums), the
# Constituent National Assembly (kr.na), the National Conference for Unification (kr.ncu), and the presidency and the
# persons who were candidates in the direct elections (kr.nec).
NATION_AREA = "146a6577-f7d3-5948-b3c3-80c92a6b247b"
NATION_UNIT = "53827e0c-81f0-5996-bb4a-a0f2c5f91576"
CONSTITUENT = "d9ce833e-d11f-5e58-8e31-e57646819eb0"
CONFERENCE = "5c2aea0b-cfdc-57dd-b578-accf09f9bfab"
PRESIDENT = "446f8277-ad32-5e45-8c63-beb1146af7be"
HELD = {
    "이승만": "609d1e3a-a788-5a87-8ed6-529b078751f6",
    "윤보선": "fc1067cc-a228-509e-ba2e-c04c7191617d",
    "박정희": "0054512b-b4f0-545e-9116-74a10c2febf4",
    "변영태": "e17adf81-0827-56b9-b697-fb40299fe79e",
}
ENGLISH = {
    "최규하": "Choi Kyu-hah",
    "전두환": "Chun Doo-hwan",
    "김구": "Kim Gu",
    "안재홍": "Ahn Jae-hong",
}
TITLE = "선거통계시스템: 역대선거 대통령선거 개표현황 (간접선거)"
WINNERS_TITLE = "선거통계시스템: 역대선거 대통령선거 당선인 명부 (간접선거)"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP09"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


NEC = key("source", "vccp09-president-indirect")
WINNERS = key("source", "epei01-president-indirect")
COLLEGE = key("body", "presidential-electoral-college")
SYSTEMS = {name: key("electoral_system", name) for name in ("assembly", "conference", "college")}


@dataclass(frozen=True)
class Vote:
    day: date
    ordinal: str
    body: str | None
    system: str
    note: str
    english: str


VOTES_HELD = (
    Vote(
        date(1948, 7, 20),
        "제1대",
        CONSTITUENT,
        "assembly",
        "The Constituent National Assembly elected the first president under article 53 of the Constitution, by secret "
        "ballot and without registered candidates: each member wrote a name on the ballot, so some people received "
        "votes without having sought the office, and Kim Gu had declared that he would not take part. The Commission "
        "notes that the one vote for Seo Jae-pil was invalidated because he held foreign nationality. It cites the "
        "Assembly's stenographic record, session 1, number 33. The seated members are stored as registered voters and "
        "the members voting as ballots cast.",
        "1948",
    ),
    Vote(
        date(1960, 8, 12),
        "제4대",
        None,
        "assembly",
        "After the April Revolution and the annulment of the March 1960 vote, the two houses of the National Assembly "
        "elected the president at a joint session, by secret ballot, under the constitution as amended in June 1960. "
        "The Commission prints the table for the 263 members on the roll. The seated members are stored as registered "
        "voters and the members voting as ballots cast.",
        "1960 (August)",
    ),
    Vote(
        date(1972, 12, 23),
        "제8대",
        CONFERENCE,
        "conference",
        "Under the Yushin constitution the National Conference for Unification elected the president by secret ballot. "
        "Park Chung-hee was the only person on the ballot, and the Commission prints the votes in favor as his votes. "
        "The members on the roll are stored as registered voters and the ballots cast as ballots cast.",
        "1972",
    ),
    Vote(
        date(1978, 7, 6),
        "제9대",
        CONFERENCE,
        "conference",
        "The National Conference for Unification elected the president by secret ballot with Park Chung-hee as the "
        "only person on the ballot; the Commission prints the votes in favor as his votes.",
        "1978",
    ),
    Vote(
        date(1979, 12, 6),
        "제10대",
        CONFERENCE,
        "conference",
        "After Park Chung-hee's death on 26 October 1979, the National Conference for Unification elected Choi "
        "Kyu-hah, then acting president, as the only person on the ballot; the Commission prints the votes in favor as "
        "his votes.",
        "1979",
    ),
    Vote(
        date(1980, 8, 27),
        "제11대",
        CONFERENCE,
        "conference",
        "After Choi Kyu-hah resigned on 16 August 1980, the National Conference for Unification elected Chun "
        "Doo-hwan as the only person on the ballot; the Commission prints the votes in favor as his votes.",
        "1980",
    ),
    Vote(
        date(1981, 2, 25),
        "제12대",
        COLLEGE,
        "college",
        "Under the constitution adopted on 22 October 1980, a college of 5,278 presidential electors chosen on "
        "11 February 1981 elected the president by secret ballot among four candidates. The Commission prints the "
        "electors on the roll as 5,277. They are stored as registered voters and the ballots cast as ballots cast.",
        "1981",
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in VOTES_HELD)]


def _ballot_form(day: date) -> dict[str, str]:
    code = f"{day:%Y%m%d}"
    return {
        "electionId": "0000000000",
        "requestURI": "/electioninfo/0000000000/vc/vccp09.jsp",
        "topMenuId": "VC",
        "secondMenuId": "VCCP09",
        "menuId": "VCCP09",
        "statementId": "VCCP09_#90",
        "oldElectionType": "0",
        "electionType": "1",
        "electionName": code,
        "electionCode": "1",
        "cityCode": "-1",
        "townCode": "-1",
    }


def _url(form: dict[str, str]) -> str:
    return f"{REPORT}?{urlencode(form)}"


def _ballot_url(day: date) -> str:
    return _url(_ballot_form(day))


def _winner_url(day: date) -> str:
    return _winner_key(f"{day:%Y%m%d}")


def fetch() -> None:
    for vote in VOTES_HELD:
        for url, form in (
            (_ballot_url(vote.day), _ballot_form(vote.day)),
            (_winner_url(vote.day), _winner_form(f"{vote.day:%Y%m%d}")),
        ):
            if url not in raw.keys(SOURCE):
                html = _get(REPORT, form)
                if "개표현황" not in html.decode() and "당선인명부" not in html.decode():
                    raise ValueError(f"{vote.day}: the report did not come back")
                raw.store(SOURCE, url, html, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


TAG = re.compile(r"<[^>]+>")


def _cells(html: str) -> list[str]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", html, re.DOTALL)
    return [unicodedata.normalize("NFC", " ".join(TAG.sub(" ", cell.replace("&nbsp;", " ")).split())) for cell in cells]


def _count(text: str) -> int:
    return int(text.split("(")[0].replace(",", "").strip())


@dataclass(frozen=True)
class Table:
    registered: int
    cast: int
    valid: int
    invalid: int
    abstained: int
    candidates: dict[str, int]


def _section(html: str, table_id: str) -> str:
    start = html.index(f'<table id="{table_id}"')
    return html[start : html.index("</table>", start)]


def table(html: str, vote: Vote) -> Table:
    section = _section(html, "table02" if vote.day.year == 1960 else "table01")
    head = section[section.index("<thead") : section.index("</thead>")]
    names = [
        unicodedata.normalize("NFC", "".join(name.split())) for name in re.findall(r"<strong>(.*?)</strong>", head)
    ]
    body = section[section.index("<tbody") :]
    cells = _cells(body[: body.index("</tbody>")])
    # registered, cast, the votes for each person, the total, invalid, abstained (then a row of shares, if any)
    width = 2 + len(names) + 3
    if len(cells) < width:
        raise ValueError(f"{vote.day}: {len(cells)} cells in {cells}")
    cells = cells[:width]
    registered, cast = _count(cells[0]), _count(cells[1])
    candidates = {name: _count(cell) for name, cell in zip(names, cells[2 : 2 + len(names)], strict=True)}
    total, invalid, abstained = (_count(cell) for cell in cells[-3:])
    if sum(candidates.values()) != total:
        raise ValueError(f"{vote.day}: candidates sum to {sum(candidates.values())}, total {total}")
    if total + invalid != cast or registered - cast != abstained:
        raise ValueError(f"{vote.day}: the table does not add up: {cells}")
    return Table(registered, cast, total, invalid, abstained, candidates)


def _check_winner(vote: Vote, found: Table) -> str:
    winner = max(found.candidates, key=lambda name: found.candidates[name])
    if vote.day.year == 1960:
        return winner
    html = raw.cached(SOURCE, _winner_url(vote.day)).path.read_text()
    cells = _cells(html[html.index("<tbody") : html.index("</tbody>")])
    at = cells.index("대통령선거")
    named = cells[at + 2].split("(")[0].strip()
    votes = _count(cells[-1])
    if named != winner or votes != found.candidates[winner]:
        raise ValueError(f"{vote.day}: winners list has {named} with {votes}, counting report {winner}")
    return winner


def parse() -> None:
    tables = {}
    for vote in VOTES_HELD:
        html = raw.cached(SOURCE, _ballot_url(vote.day)).path.read_text()
        found = table(html, vote)
        _check_winner(vote, found)
        tables[vote.day] = found
        print(vote.day, found)
    registry(tables).write()
    for vote in VOTES_HELD:
        build(vote, tables[vote.day]).write()


def emit() -> None:
    package.run("load", packages())


def _person(name: str) -> str:
    return HELD.get(name) or key("person", name)


def registry(tables: dict[date, Table]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The presidential electoral college of 1981, the electoral systems of the indirect presidential elections, "
        "and the people who received votes in them",
    )
    p.external |= {KOREA}
    p.add("body", id=COLLEGE, polity_id=KOREA, kind="electoral_college")
    p.name(COLLEGE, "대통령선거인단", "ko", "official")
    p.name(COLLEGE, "Presidential electoral college", "en", "english")
    systems = {
        "assembly": (
            "국회의 대통령 선거",
            "Election of the President by the National Assembly",
            (
                "Under article 53 of the 1948 Constitution, the National Assembly elected the President by secret ballot, "
                "with two thirds of the members present and two thirds of the votes of those present required on the first "
                "ballots and a runoff by simple majority after them."
            ),
        ),
        "conference": (
            "통일주체국민회의의 대통령 선거",
            "Election of the President by the National Conference for Unification",
            (
                "Under the Yushin constitution of 1972, the National Conference for Unification elected the President by "
                "secret ballot, and a majority of all its members elected."
            ),
        ),
        "college": (
            "대통령선거인단의 대통령 선거",
            "Election of the President by the presidential electoral college",
            (
                "Under the constitution of 1980, an electoral college of at least 5,000 members chosen by the voters elected "
                "the President by secret ballot, and a majority of all electors elected."
            ),
        ),
    }
    for name, (ko, en, note) in systems.items():
        system = SYSTEMS[name]
        p.add("electoral_system", id=system, polity_id=KOREA, rules={"formula": "fptp"}, rules_version=1)
        p.name(system, ko, "ko", "official")
        p.name(system, en, "en", "english")
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=note, lang="en")
    p.add(
        "source",
        id=NEC,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=LANDING,
        license=LICENSE,
        attribution="중앙선거관리위원회",
    )
    p.add(
        "source",
        id=WINNERS,
        kind="official_body",
        publisher=PUBLISHER,
        title=WINNERS_TITLE,
        url=f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01",
        license=LICENSE,
        attribution="중앙선거관리위원회",
    )
    names = sorted({name for found in tables.values() for name in found.candidates} - set(HELD))
    p.external |= {PRESIDENT, *HELD.values()}
    for name in names:
        person = key("person", name)
        p.add("person", id=person)
        p.name(person, name, "ko", "official")
        if name in ENGLISH:
            p.name(person, ENGLISH[name], "en", "english")
    return p


def build(vote: Vote, found: Table) -> Package:
    day = vote.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {vote.english} indirect election of the President of South Korea on {day:%-d %B %Y}, from the National "
        "Election Commission's statistics system",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {KOREA, NATION_AREA, NATION_UNIT, PRESIDENT}
    p.external |= {HELD[name] for name in found.candidates if name in HELD}
    if vote.body and vote.body != COLLEGE:
        p.external.add(vote.body)
    p.artifact(raw.cached(SOURCE, _ballot_url(day)), NEC)
    if day.year != 1960:
        p.artifact(raw.cached(SOURCE, _winner_url(day)), WINNERS)
    election = key("election", day)
    path = f"kr/{day}-indirect-presidential"
    p.add("election", id=election, polity_id=KOREA, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{vote.ordinal} 대통령선거 (간접선거)", "ko", "official")
    p.name(election, f"{vote.english} South Korean indirect presidential election", "en", "english")
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENT,
        body_id=vote.body,
        district_area_id=NATION_AREA,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEMS[vote.system],
        default_result_kind="counted",
    )
    p.name(contest, f"Election of the President, {vote.english} (indirect)", "en", "english")
    p.name(contest, "대통령", "ko", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=NEC,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=vote.note, lang="en")
    rows: list[tuple[str | None, int, int]] = [
        (None, REGISTERED, found.registered),
        (None, CAST, found.cast),
        (None, VALID, found.valid),
        (None, INVALID, found.invalid),
    ]
    winner = max(found.candidates, key=lambda name: found.candidates[name])
    for position, (name, votes) in enumerate(found.candidates.items(), 1):
        option = key("option", day, name)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_position=position)
        p.add(
            "candidacy",
            id=key("candidacy", day, name),
            person_id=_person(name),
            option_id=option,
            role="head",
            ballot_name=name,
            is_independent=False,
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if name == winner else "lost",
            seats=1 if name == winner else None,
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        rows.append((option, VOTES, votes))
    for measure, value in ((REGISTERED, found.registered), (CAST, found.cast), (VALID, found.valid)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    p.frame(
        "result_value",
        pl.DataFrame(
            [(result_set, NATION_UNIT, option, measure, value, 0) for option, measure, value in rows],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
    )
    return p
