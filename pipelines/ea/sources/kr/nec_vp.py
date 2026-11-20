"""South Korea's two vice-presidential elections in the National Assembly, 1948 and 1951.

Under the 1948 Constitution (art. 53) the National Assembly elected the President and the Vice President by secret
ballot, with two thirds of the members present and two thirds of the votes of those present. The Constituent
Assembly elected the first Vice President on 20 July 1948, and the second Assembly elected his successor on 16 May
1951 after he resigned. The National Election Commission's statistics system prints each vote in its table of
vice-presidential elections held (역대선거 실시상황, BIGI06, election type 2): the members seated and present, the
valid and invalid votes, the abstentions, and the votes for each person named. The vote was taken in the chamber, so
there is no breakdown by sido or sigungu; the votes are stored on the national unit.

The direct elections of 1952, 1956, and 1960 appear in the same table only as national candidate totals and are not
built here.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.kr.nec import BASE, LICENSE, PUBLISHER, REPORT

SOURCE = "kr.nec_vp"
KOREA = str(uuid5(package.NAMESPACE, "polity\x1fKR"))
# Rows production already holds: the national area (kr.nec), the national reporting unit (kr.referendums), and the
# Constituent National Assembly and the National Assembly (kr.na).
NATION_AREA = "146a6577-f7d3-5948-b3c3-80c92a6b247b"
NATION_UNIT = "53827e0c-81f0-5996-bb4a-a0f2c5f91576"
CONSTITUENT = "d9ce833e-d11f-5e58-8e31-e57646819eb0"
ASSEMBLY = "4b483905-0264-5e5b-b0e3-0be5fbbdc8a3"
TITLE = "선거통계시스템: 역대선거 실시상황 부통령선거"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=BI&secondMenuId=BIGI06"
FORM = {
    "electionId": "0000000000",
    "requestURI": "/electioninfo/0000000000/bi/bigi06.jsp",
    "topMenuId": "BI",
    "secondMenuId": "BIGI06",
    "menuId": "BIGI06",
    "statementId": "BIGI06",
    "electionType": "2",
}
URL = f"{REPORT}?{urlencode(FORM)}"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


NEC = key("source", "bigi06-vice-president")
OFFICE = key("office", "vice-president")
SYSTEM = key("electoral_system", "assembly")


@dataclass(frozen=True)
class Vote:
    day: date
    ordinal: str  # as the table heads it
    body: str
    hanja: dict[str, str]  # names the table prints in hangul, with the hanja it prints beside them
    english: dict[str, str]
    round_number: int
    note: str


VOTES_HELD = (
    Vote(
        date(1948, 7, 20),
        "초대",
        CONSTITUENT,
        {"이시영": "李始榮", "김구": "金九", "이귀수": "李龜洙"},
        {"이시영": "Yi Si-yeong", "김구": "Kim Gu", "이귀수": "Yi Gwi-su"},
        2,
        "The Constituent Assembly voted under article 53 of the Constitution, by secret ballot and without registered "
        "candidates: each member wrote a name on the ballot, so some people received votes without having sought the "
        "office. The first ballot did not meet the requirement of two thirds of the members present and two thirds of "
        "the votes of those present, and Yi Si-yeong was elected on the second ballot. The National Election "
        "Commission prints the second ballot only; it cites the Assembly's stenographic record, session 1, number 33. "
        "The seated members are stored as registered voters and the members present as ballots cast.",
    ),
    Vote(
        date(1951, 5, 16),
        "제2대",
        ASSEMBLY,
        {"김성수": "金性洙", "이갑성": "李甲成"},
        {"김성수": "Kim Seong-su", "이갑성": "Yi Gap-seong"},
        1,
        "The National Assembly elected a successor to Vice President Yi Si-yeong, who had resigned, by secret ballot "
        "under article 53 of the Constitution. The National Election Commission prints one ballot. The seated members "
        "are stored as registered voters and the members present as ballots cast.",
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in VOTES_HELD)]


def _check(path: Path) -> None:
    if "[부통령선거][초대(1948.07.20)]" not in path.read_text():
        raise ValueError("the vice-presidential table did not come back")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html", check=_check)


TAG = re.compile(r"<[^>]+>")
PERSON = re.compile(r"(\S+) \(([\u4e00-\u9fff]+)\)")


def _cells(html: str) -> list[str]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", html, re.DOTALL)
    # The system prints some hanja as CJK compatibility ideographs (李 as U+F9E1); NFC folds them to unified ones.
    return [unicodedata.normalize("NFC", " ".join(TAG.sub(" ", cell.replace("&nbsp;", " ")).split())) for cell in cells]


def _count(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass(frozen=True)
class Table:
    seated: int
    present: int
    valid: int
    invalid: int
    cast: int
    abstained: int
    candidates: dict[str, int]


def table(html: str, vote: Vote) -> Table:
    marker = f"[부통령선거][{vote.ordinal}({vote.day:%Y.%m.%d})]"
    start = html.index(marker)
    end = html.find("[부통령선거][", start + len(marker))
    section = html[start : end if end >= 0 else len(html)]
    body = section[section.index("<tbody") :]
    body = body[: body.index("</tbody>")]
    head = section[section.index("<thead") : section.index("</thead>")]
    printed = [match.groups() for cell in _cells(head) if (match := PERSON.fullmatch(cell))]
    names = [name for name, _ in printed]
    for name, hanja in printed:
        if vote.hanja.get(name) != hanja:
            raise ValueError(f"{vote.day}: {name} ({hanja}) is not the expected {vote.hanja.get(name)}")
    if names != list(vote.hanja):
        raise ValueError(f"{vote.day}: the table names {names}, expected {list(vote.hanja)}")
    numbers = _cells(body)
    # seated, present, valid, invalid, cast, abstained, turnout, valid share, candidates..., total
    if len(numbers) != 8 + len(names) + 1:
        raise ValueError(f"{vote.day}: {len(numbers)} cells in {numbers}")
    seated, present, valid, invalid, cast, abstained = (_count(cell) for cell in numbers[:6])
    candidates = {name: _count(cell) for name, cell in zip(names, numbers[8:-1], strict=True)}
    total = _count(numbers[-1])
    if sum(candidates.values()) != total or total != valid:
        raise ValueError(f"{vote.day}: candidates sum to {sum(candidates.values())}, total {total}, valid {valid}")
    if valid + invalid != cast or cast != present or seated - present != abstained:
        raise ValueError(f"{vote.day}: the table does not add up: {numbers}")
    if f"{100 * cast / seated:.1f}" != numbers[6] or f"{100 * valid / cast:.1f}" != numbers[7]:
        raise ValueError(f"{vote.day}: the printed rates {numbers[6:8]} do not match the counts")
    return Table(seated, present, valid, invalid, cast, abstained, candidates)


def parse() -> None:
    html = raw.cached(SOURCE, URL).path.read_text()
    tables = {vote.day: table(html, vote) for vote in VOTES_HELD}
    start = html.index('<table id="table01"')
    summary = " ".join(_cells(html[start : html.index("</table>", start)]))
    for vote in VOTES_HELD:
        found = tables[vote.day]
        winner = max(found.candidates, key=lambda name: found.candidates[name])
        if not re.search(rf"{vote.ordinal} .*?{vote.day:%Y.%m.%d}\S* {winner} 국회선출", summary):
            raise ValueError(f"{vote.day}: the summary table does not name {winner} elected by the Assembly")
    for day, found in tables.items():
        print(day, found)
    registry().write()
    for vote in VOTES_HELD:
        build(vote, tables[vote.day]).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The vice presidency of the First Republic of Korea, the electoral system of its election by the National "
        "Assembly, and the people who received votes in the Assembly's elections of 1948 and 1951",
    )
    p.external |= {KOREA}
    p.add("office", id=OFFICE, polity_id=KOREA, kind="other")
    p.name(OFFICE, "부통령", "ko", "official")
    p.name(OFFICE, "Vice President of the Republic of Korea", "en", "english")
    p.add("electoral_system", id=SYSTEM, polity_id=KOREA, rules={"formula": "fptp"}, rules_version=1)
    p.name(SYSTEM, "국회의 부통령 선거", "ko", "official")
    p.name(SYSTEM, "Election of the Vice President by the National Assembly", "en", "english")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under article 53 of the 1948 Constitution, the National Assembly elected the Vice President by secret "
        "ballot. Election required the presence of two thirds of the members and two thirds of the votes of those "
        "present; failing that on the first ballot, a second ballot was held, and failing that, a runoff between the "
        "two leading persons by simple majority. The 1952 amendment moved the election to a direct popular vote.",
        lang="en",
    )
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
    for vote in VOTES_HELD:
        for name, hanja in vote.hanja.items():
            person = key("person", vote.day, name)
            p.add("person", id=person)
            p.name(person, name, "ko", "official")
            p.name(person, hanja, "ko-Hani", "native", preferred=False)
            p.name(person, vote.english[name], "en", "english")
    return p


def build(vote: Vote, found: Table) -> Package:
    day = vote.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The National Assembly's election of the Vice President on {day:%-d %B %Y}, from the National Election "
        "Commission's statistics system",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {KOREA, NATION_AREA, NATION_UNIT, vote.body}
    p.artifact(raw.cached(SOURCE, URL), NEC)
    election = key("election", day)
    path = f"kr/{day}-vice-presidential"
    p.add("election", id=election, polity_id=KOREA, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{vote.ordinal.replace('초대', '제1대')} 부통령선거", "ko", "official")
    p.name(election, f"{day.year} South Korean vice-presidential election", "en", "english")
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=OFFICE,
        body_id=vote.body,
        district_area_id=NATION_AREA,
        magnitude=1,
        round_number=vote.round_number,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"Election of the Vice President by the National Assembly, {day.year}", "en", "english")
    p.name(contest, "부통령", "ko", "official")
    p.add("slug", path=f"{path}/vice-president", entity_id=contest, is_current=True)
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
        (None, REGISTERED, found.seated),
        (None, CAST, found.cast),
        (None, VALID, found.valid),
        (None, INVALID, found.invalid),
    ]
    winner = max(found.candidates, key=lambda name: found.candidates[name])
    for position, (name, votes) in enumerate(found.candidates.items(), 1):
        option = key("option", day, name)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_position=position)
        candidacy = key("candidacy", day, name)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=key("person", day, name),
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
    for measure, value in ((REGISTERED, found.seated), (CAST, found.cast), (VALID, found.valid)):
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
