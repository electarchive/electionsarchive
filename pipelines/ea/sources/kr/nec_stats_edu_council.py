"""South Korea's education council member elections (교육의원선거) 2006-2022 as `kr.nec_stats` date packages.

Education council members sat on the education committee of a metropolitan or provincial council. Jeju elected five
by constituency in 2006, 2010, 2014, 2018, and 2022, and every other province elected its members once, in 2010
(82 seats in all, terms to 30 June 2014). The office lapsed on 30 June 2026 and no election was held that year.

The statistics system lists subelection code 10 (교육의원) for those days. Like a metropolitan or provincial council
contest, each constituency is counted by counting office (구시군위원회), by neighborhood (개표현황(읍면동별), VCCP04), with the early, absentee, and overseas rows; a province's page by counting office (개표현황, VCCP09) gives the commission's own constituency totals, and the
winners' list (당선인명부, EPEI01) names who was elected. One package per day holds the day's contests, and one
registry package holds the bodies, the electoral systems, and the two sources.
"""

import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid5

import httpx

from ea import package, raw
from ea.package import Package
from ea.sources.kr import local, local_dong, nec
from ea.sources.kr.na import _number, _numeric, table
from ea.sources.kr.nec import BASE, REPORT
from ea.sources.kr.nec_stats import (
    KOREA,
    LICENSE,
    METHOD,
    PUBLISHER,
    REASONS,
    SYSTEMS,
    Rec,
    _apart,
    _channels_split,
    _labels,
    _write_units,
    region,
)

PIPELINE = "kr.nec_stats_edu_council"
SOURCE = "kr.nec_stats"
RAW = PIPELINE
REGISTRY = f"{SOURCE}/registry-education-council"
SELECT = f"{BASE}/bizcommon/selectbox"
CODE = "10"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP09"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
TITLE = "선거통계시스템: 역대선거 교육의원선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 교육의원선거 당선인 명부"
DAYS = ("20060531", "20100602", "20140604", "20180613", "20220601")
SPECIAL = {2: "aggregate", 3: "postal_district", 4: "abroad_mission"}
TOTALS = {"합계", "계", "소계"}
SEAT_NOTE = (
    "Education council members were elected by residents of each constituency, each voter naming one candidate, and "
    "the candidates with the most valid votes fill the constituency's seats (Local Education Autonomy Act, "
    "arts. 4 to 7 and the addenda of Act No. 10046 of 2010)."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([PIPELINE, kind, *(str(part) for part in parts)])))


COUNT_SOURCE = key("source", "counts")
WINNER_SOURCE = key("source", "winners")


def _form(kind: str, day: str, city: str, town: str = "-1", sgg: str = "-1") -> dict[str, str]:
    if kind == "town":
        return local_dong.form(day, CODE, city, "-1", "-1", town, sgg)
    form = local._form("winners" if kind == "winners" else "count", day, city, CODE)
    if kind == "county":
        form["statementId"] = "VCCP09_#2"
    return form


def address(kind: str, day: str, city: str, town: str = "-1", sgg: str = "-1") -> str:
    return f"{REPORT}?{urlencode(_form(kind, day, city, town, sgg))}"


def _get(url: str, data: dict[str, str] | None = None) -> bytes:
    headers = {"User-Agent": raw.USER_AGENT, "Referer": BASE}
    for attempt in range(8):
        try:
            response = (
                httpx.get(url, timeout=120, headers=headers)
                if data is None
                else httpx.post(url, data=data, timeout=180, headers=headers)
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == 7:
                raise
            time.sleep(4 * (attempt + 1))
    raise AssertionError


def _pairs(path: str, **query: str) -> dict[str, str]:
    url = f"{SELECT}/{path}?{urlencode({'electionId': '0000000000', **query})}"
    if url not in raw.keys(RAW):
        for _ in range(10):
            content = _get(url)
            try:
                json.loads(content)
            except ValueError:
                time.sleep(3)
                continue
            raw.store(RAW, url, content, suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
            break
        else:
            raise ValueError(f"{url}: no JSON")
    body = json.loads(raw.cached(RAW, url).path.read_text())["jsonResult"]["body"]
    return {str(row["CODE"]): str(row["NAME"]) for row in body}


def cities(day: str) -> dict[str, str]:
    return _pairs("selectbox_cityCodeBySgJson_Old.json", electionCode=day, subElectionCode=CODE)


def towns(day: str, city: str) -> dict[str, str]:
    return _pairs("selectbox_townCodeBySgJson_Old.json", electionCode=day, cityCode=city, subElectionCode=CODE)


def sggs(day: str, town: str) -> dict[str, str]:
    return _pairs("selectbox_getSggTownCodeJson_Old.json", electionName=day, electionCode=CODE, townCode=town)


def _complete(kind: str, html: str) -> bool:
    start = html.find('id="table01"')
    if start < 0 or "<tbody" not in html[start:]:
        return False
    if kind in {"winners", "county"} or "조회된 자료가 없습니다" in html[start:]:
        return True
    try:
        rows = local_dong.parse_page(html)["rows"]
    except ValueError:
        return False
    return not rows or all(any(row["labels"]) for row in rows)


Job = tuple[str, str, str, str, str]


def _page(job: Job) -> None:
    url = address(*job)
    if url in raw.keys(RAW):
        return
    for _ in range(30):
        content = _get(REPORT, _form(*job))
        if _complete(job[0], content.decode(errors="replace")):
            raw.store(RAW, url, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
            return
        time.sleep(3)
    raise ValueError(f"{url}: the page keeps coming back without its table or its unit names")


def _attempt(job: Job) -> str | None:
    try:
        _page(job)
    except (ValueError, httpx.HTTPError) as error:
        return str(error)
    return None


def jobs(day: str) -> list[Job]:
    found: list[Job] = []
    for city in cities(day):
        found += [("winners", day, city, "-1", "-1"), ("county", day, city, "-1", "-1")]
        for town in towns(day, city):
            found += [("town", day, city, town, sgg) for sgg in sggs(day, town)]
    return found


def fetch(days: tuple[str, ...] = DAYS, threads: int = 4) -> None:
    for day in days:
        found = jobs(day)
        with ThreadPoolExecutor(threads) as pool:
            failed = [error for error in pool.map(_attempt, found) if error]
        for error in failed:
            print(error, flush=True)
        print(f"{day}: {len(found)} pages", flush=True)


# --- reading the cache ---


def _html(url: str) -> str:
    return raw.cached(RAW, url).path.read_text(errors="replace")


def _has_rows(html: str) -> bool:
    start = html.find('id="table01"')
    return start >= 0 and "조회된 자료가 없습니다" not in html[start:]


def county(html: str) -> dict[str, dict[str, Any]]:
    """A province's page by counting office: per constituency, its candidates and the figures of each office."""
    found: dict[str, dict[str, Any]] = {}
    name = ""
    for row in table(html):
        if len(row) > 6 and row[0] and not row[1] and "계" in row and not any(_numeric(cell) for cell in row[2:]):
            valid = row.index("계")
            slots = [index for index, cell in enumerate(row[4:valid]) if cell]
            name = row[0]
            found[name] = {"names": [row[4 + index] for index in slots], "slots": slots, "valid": valid, "rows": {}}
        elif name and len(row) > 6 and not row[0] and row[1] and _numeric(row[2]):
            district = found[name]
            valid = district["valid"]
            if len(row) < valid + 3 or not _numeric(row[valid]):
                raise ValueError(f"{name}: row {row}")
            district["rows"][row[1]] = {
                "registered": _number(row[2]),
                "ballots": _number(row[3]),
                "votes": [_number(row[4 + index]) if row[4 + index] else 0 for index in district["slots"]],
                "valid": _number(row[valid]),
                "invalid": _number(row[valid + 1]),
            }
    return found


def winners(day: str, city: str) -> dict[str, list[tuple[str, int | None]]]:
    """The winners' list of a province: per constituency, each winner and the votes printed for them, if any."""
    rows = table(_html(address("winners", day, city)))
    head = next(row for row in rows if row and row[0] == "선거구명")
    found: dict[str, list[tuple[str, int | None]]] = {}
    for row in rows:
        if len(row) != len(head) or row is head:
            continue
        cells = dict(zip(head, row, strict=True))
        person = re.sub(r"\s*\(.*\)$", "", cells["성명 (한자)"]).replace(" ", "")
        votes = re.match(r"[\d,]+", cells["득표수 (득표율)"])
        found.setdefault(cells["선거구명"], []).append((person, int(votes.group().replace(",", "")) if votes else None))
    return found


@dataclass
class Contest:
    ref: str
    sido: str
    sgg: str
    names: list[str]
    elected: list[bool]
    declared: list[int | None]
    recs: list[Rec] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tacit: bool = False


@dataclass
class Day:
    day: str
    contests: list[Contest]
    urls: list[str]


CHECKS: Counter[str] = Counter()


def _candidates(page: dict[str, Any]) -> list[str]:
    return [second or first for first, second in page["options"]]


def contests(day: str) -> Day:
    found: list[Contest] = []
    for city in cities(day):
        base = local._sido(city)
        county_url = address("county", day, city)
        winners_url = address("winners", day, city)
        districts = county(_html(county_url))
        won = winners(day, city)
        pages: defaultdict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for town, town_name in towns(day, city).items():
            for sgg, sgg_name in sggs(day, town).items():
                pages[sgg_name].append((town_name, address("town", day, city, town, sgg), sgg))
        for sgg_name in sorted(set(won) | set(districts)):
            if sgg_name not in won:
                raise ValueError(f"{day} {sgg_name}: a count page without a winners' list entry")
            winning = won[sgg_name]
            urls = [winners_url]
            notes: list[str] = []
            if sgg_name not in districts or not districts[sgg_name]["rows"]:
                if any(votes is not None for _, votes in winning):
                    raise ValueError(f"{day} {sgg_name}: winners with votes but no count")
                found.append(
                    Contest(
                        f"{city}:{sgg_name}",
                        base,
                        sgg_name,
                        [person for person, _ in winning],
                        [True] * len(winning),
                        [None] * len(winning),
                        urls=urls,
                        tacit=True,
                    )
                )
                continue
            district = districts[sgg_name]
            urls.append(county_url)
            names: list[str] = district["names"]
            if len(set(names)) != len(names):
                raise ValueError(f"{day} {sgg_name}: two candidates share a name {names}")
            flags = [name in {person for person, _ in winning} for name in names]
            if sum(flags) != len(winning):
                raise ValueError(f"{day} {sgg_name}: winners {winning} among {names}")
            recs: list[Rec] = []
            office_sums: dict[str, list[int]] = {}
            for town_name, url, _ in sorted(pages[sgg_name]):
                html = _html(url)
                urls.append(url)
                if not _has_rows(html):
                    CHECKS["empty page"] += 1
                    print(f"{day} {sgg_name} {town_name}: no count", flush=True)
                    continue
                parsed = local_dong.parse_page(html)
                order = _candidates(parsed)
                if sorted(order) != sorted(names):
                    raise ValueError(f"{url}: candidates {order} differ from {names}")
                _, leaves = local_dong.units(parsed)
                if not leaves:
                    raise ValueError(f"{url}: no unit counts")
                sums = [0] * len(names)
                for leaf in leaves:
                    votes = [leaf["votes"][order.index(name)] for name in names]
                    sums = [a + b for a, b in zip(sums, votes, strict=True)]
                    channel = leaf["channel"]
                    special = bool(leaf.get("special"))
                    recs.append(
                        Rec(
                            (base, nec._town(town_name), leaf["unit"]),
                            leaf["registered"],
                            leaf["ballots"],
                            leaf["valid"],
                            leaf["invalid"],
                            votes,
                            channel,
                            "aggregate"
                            if leaf["unit"].startswith("잘못")
                            else SPECIAL.get(channel, "standard")
                            if special
                            else "standard",
                        )
                    )
                office_sums[nec._town(town_name)] = sums
            for office, figures in district["rows"].items():
                if office in TOTALS:
                    continue
                town = nec._town(office)
                if town not in office_sums:
                    recs.append(
                        Rec(
                            (base, town),
                            figures["registered"],
                            figures["ballots"],
                            figures["valid"],
                            figures["invalid"],
                            list(figures["votes"]),
                        )
                    )
                    CHECKS["office only"] += 1
                    if not any("counting office" in note for note in notes):
                        notes.append(
                            "The commission prints no count below the counting office for this constituency, so "
                            "its units are the counting offices."
                        )
                elif office_sums[town] != figures["votes"]:
                    CHECKS["office differs"] += 1
                    print(
                        f"{day} {sgg_name} {office}: units sum to {office_sums[town]}, the county page to {figures['votes']}",
                        flush=True,
                    )
            grand = [sum(column) for column in zip(*(rec.votes for rec in recs), strict=True)]
            total = next((row for office, row in district["rows"].items() if office in TOTALS), None)
            if total is not None and total["votes"] != grand:
                CHECKS["district differs"] += 1
                print(f"{day} {sgg_name}: units sum to {grand}, the printed total to {total['votes']}", flush=True)
            declared: list[int | None] = []
            for index, name in enumerate(names):
                vote = next((votes for person, votes in winning if person == name), None)
                declared.append(vote if flags[index] else None)
                if flags[index] and vote is not None and vote != grand[index]:
                    CHECKS["winner differs"] += 1
                    notes.append(
                        f"The winners' list prints {vote:,} votes for {name}; the count pages print "
                        f"{grand[index]:,}, which is the figure stored."
                    )
                    print(f"{day} {sgg_name}: winners' list {vote} for {name}, counts {grand[index]}", flush=True)
            found.append(Contest(f"{city}:{sgg_name}", base, sgg_name, names, flags, declared, recs, urls, notes))
    return Day(day, found, sorted({url for contest in found for url in contest.urls}))


def days() -> list[Day]:
    return [contests(day) for day in DAYS]


def package_key(day: str) -> str:
    return f"{SOURCE}/{date(int(day[:4]), int(day[4:6]), int(day[6:]))}-education-council"


def _body(sido: str) -> str:
    return key("body", sido)


def _titles(day: str, contest: Contest) -> tuple[str, str]:
    english = nec.SIDO[contest.sido][2]
    number = re.search(r"제(\d+)선거구$", contest.sgg)
    label = (
        f"constituency {number.group(1)}"
        if number
        else f"constituency ({contest.sgg.rsplit(' ', 2)[-2]} {contest.sgg.rsplit(' ', 1)[-1]})"
    )
    return f"{day[:4]}년 {contest.sgg}", f"{day[:4]} {english} education council member election, {label}"


def build_registry(every: list[Day]) -> Package:
    p = Package(
        REGISTRY,
        PIPELINE,
        "The bodies and electoral systems of South Korea's education council member elections in the National "
        "Election Commission's election statistics",
    )
    p.external.add(KOREA)
    for source, title, landing, notes in (
        (
            COUNT_SOURCE,
            TITLE,
            LANDING,
            (
                "개표현황 pages of the election statistics system for the education council member elections: one "
                "page per province by counting office (구시군위원회), and one page per counting office and "
                "constituency by neighborhood."
            ),
        ),
        (
            WINNER_SOURCE,
            WINNERS_TITLE,
            WINNERS_LANDING,
            "당선인명부 pages of the election statistics system for the education council member elections.",
        ),
    ):
        p.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=title,
            url=landing,
            license=LICENSE,
            license_url=nec.BASE,
            attribution="중앙선거관리위원회 (National Election Commission)",
            notes=notes,
        )
    for sido in sorted({contest.sido for day in every for contest in day.contests}):
        owner = region(sido)
        p.external.add(owner)
        p.add("body", id=_body(sido), polity_id=owner, kind="council")
        p.name(_body(sido), f"{nec.SIDO[sido][1]} 교육의원", "ko", "official")
        p.name(_body(sido), f"{nec.SIDO[sido][2]} education council members", "en", "official", False)
    used = {
        "sntv" if len(contest.names) and sum(contest.elected) > 1 else "fptp"
        for day in every
        for contest in day.contests
    }
    for code in sorted(used):
        rules, english, note = SYSTEMS[code]
        sid = key("system", code)
        p.add("electoral_system", id=sid, polity_id=KOREA, valid_from=None, valid_to=None, rules=rules, rules_version=1)
        p.name(sid, english, "en", "official")
        p.add("note", id=key("note", sid), entity_id=sid, kind="methodology", body_md=note, lang="en")
    for url in sorted({url for day in every for url in day.urls}):
        p.uses(raw.cached(RAW, url))
    return p


def build_day(day: Day) -> Package:
    held = date(int(day.day[:4]), int(day.day[4:6]), int(day.day[6:]))
    p = Package(
        package_key(day.day),
        PIPELINE,
        f"South Korea's {held} education council member elections from the National Election Commission's "
        "statistics system, at the finest unit it prints",
        depends=[REGISTRY],
    )
    p.external.add(KOREA)
    p.external.update(("kr.nec_sido", "kr.nec_sigungu", "kr.nec_unit"))
    for url in day.urls:
        p.artifact(raw.cached(RAW, url), WINNER_SOURCE if "EPEI01" in url else COUNT_SOURCE)
    election = key("election", day.day)
    p.add(
        "election",
        id=election,
        polity_id=KOREA,
        kind="local",
        date_from=held,
        date_to=held,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{day.day[:4]}년 {held.month}월 {held.day}일 교육의원선거", "ko", "official")
    p.name(election, f"{held} South Korean education council member elections", "en", "official", False)
    made: dict[tuple[str, ...], str] = {}
    all_recs = [rec for contest in day.contests for rec in contest.recs]
    for contest in day.contests:
        _channels_split(contest.recs)
        _apart(contest.recs)
        _labels(contest.recs)
    _write_units(p, f"edc:{day.day}", all_recs, made)
    for contest in day.contests:
        seats = max(sum(contest.elected), 1)
        c = key("contest", day.day, contest.ref)
        p.add(
            "contest",
            id=c,
            election_id=election,
            kind="body_seats",
            office_id=None,
            body_id=_body(contest.sido),
            magnitude=seats,
            round_number=1,
            electoral_system_id=key("system", "sntv" if seats > 1 else "fptp"),
            default_result_kind="tacit" if contest.tacit else "counted",
        )
        korean, english = _titles(day.day, contest)
        p.name(c, korean, "ko", "official")
        p.name(c, english, "en", "official", False)
        for note in contest.notes:
            p.add("note", id=key("note", c, note[:40]), entity_id=c, kind="caveat", body_md=note, lang="en")
        rs = key("result_set", day.day, contest.ref)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=c,
            stage="certified",
            kind="tacit" if contest.tacit else "counted",
            source_id=WINNER_SOURCE if contest.tacit else COUNT_SOURCE,
            status="active",
            is_default=True,
            completeness=None if contest.tacit else 1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=f"{METHOD} {SEAT_NOTE}", lang="en")
        options: list[str] = []
        for number, (person, won, vote) in enumerate(
            zip(contest.names, contest.elected, contest.declared, strict=True), 1
        ):
            o = key("option", day.day, contest.ref, number)
            options.append(o)
            p.add(
                "contest_option",
                id=o,
                contest_id=c,
                kind="candidate",
                party_id=None,
                ballot_position=number,
                ballot_label=person,
            )
            person_id = key("person", day.day, contest.ref, number)
            p.add("person", id=person_id)
            p.name(person_id, person, "ko", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day.day, contest.ref, number),
                person_id=person_id,
                option_id=o,
                role="head",
                ballot_name=person,
                member_of_party_id=None,
                is_independent=True,
            )
            p.add("outcome", result_set_id=rs, option_id=o, kind="elected" if won else "lost", seats=1 if won else None)
            if vote is not None:
                p.add("declared", result_set_id=rs, option_id=o, measure=1, value=vote)
        for rec in contest.recs:
            missing = rec.registered == 0 and rec.ballots == 0
            figures = (
                *(() if missing else ((2, None, rec.registered), (3, None, rec.ballots))),
                (4, None, rec.valid),
                (5, None, rec.invalid),
                *((1, options[k], v) for k, v in enumerate(rec.votes)),
            )
            unit = made[
                rec.scheme
                or ("kr.nec_unit" if len(rec.path) >= 3 else ("kr.nec_sido", "kr.nec_sigungu")[len(rec.path) - 1]),
                *rec.path,
            ]
            for measure, option, value in figures:
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=option,
                    channel=rec.channel,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
    return p


def packages() -> list[str]:
    return [REGISTRY, *(package_key(day) for day in DAYS)]


def parse() -> None:
    every = days()
    build_registry(every).write()
    held: defaultdict[str, int] = defaultdict(int)
    for day in every:
        p = build_day(day)
        for code, count, _ in package.check(p.write()):
            held[code] += count
            if code in REASONS:
                p.accept(code, f"{count:,} {REASONS[code]}")
        if p.accepted:
            p.write()
        print(f"{p.key}: {len(day.contests)} contests, accepted {sorted(p.accepted)}", flush=True)
    print(dict(CHECKS), flush=True)


def emit() -> None:
    package.run("load", packages())
