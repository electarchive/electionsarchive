"""South Korea's education superintendent elections (교육감선거) 2007-2026 as `kr.nec_stats` date packages.

The statistics system prints each superintendent contest like a governor's: one page per counting office
(구시군위원회) of the province, by polling district (개표현황(투표구별), VCCP08) up to 2010 and by neighborhood
(개표현황(읍면동별), VCCP04) from 2014, with the early, absentee, and overseas rows; and one page per province by
counting office (개표현황, VCCP09), which gives the commission's own province totals. The 2026 counts are printed by
counting office only so far. The winners' list (당선인명부, EPEI01) names who was elected.

The days are those the system lists subelection code 11 (교육감) for under its local election selectors: the
superintendents elected out of cycle in 2007-2009 and the regular local election days of 2010, 2014, 2018, 2022, and
2026. The by-election days since 2010 are built by `kr.nec_stats_byelections`. One package per day holds the day's
superintendent contests, and one registry package holds the offices, the electoral system, and the two sources.
"""

import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode
from uuid import uuid5

import httpx

from ea import package, raw
from ea.package import Package
from ea.sources.kr import local, local_dong, local_head, nec
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

PIPELINE = "kr.nec_stats_education"
SOURCE = "kr.nec_stats"
RAW = "kr.nec_stats_education"
REGISTRY = f"{SOURCE}/registry-education"
SELECT = f"{BASE}/bizcommon/selectbox"
CODE = "11"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP08"
WINNERS_LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=EP&secondMenuId=EPEI01"
TITLE = "선거통계시스템: 역대선거 교육감선거 개표현황"
WINNERS_TITLE = "선거통계시스템: 역대선거 교육감선거 당선인 명부"
DAYS = (
    "20070214",
    "20071219",
    "20080625",
    "20080723",
    "20080730",
    "20081217",
    "20090408",
    "20090429",
    "20100602",
    "20140604",
    "20180613",
    "20220601",
    "20260603",
)
MERGED = "29+46"
SPECIAL = {2: "aggregate", 3: "postal_district", 4: "abroad_mission"}
SYSTEM_NOTE = (
    "The candidate with the most valid votes is elected superintendent of education of the province (Local Education "
    "Autonomy Act, art. 43, and the Public Official Election Act, art. 187). Candidates stand without a party."
)
FINEST_NOTE = (
    "The commission prints the 2026 counts by city, county, and district only, so these are the finest units of this "
    "election."
)
SEAT_NOTE = "Superintendents of education have been elected by direct vote since 2007; each province elects one."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([PIPELINE, kind, *(str(part) for part in parts)])))


COUNT_SOURCE = key("source", "counts")
WINNER_SOURCE = key("source", "winners")


def count_page(day: str) -> tuple[str, str]:
    """The menu and statement that carry the finest count the commission prints for the day."""
    if day < "20100000":
        return "VCCP08", "VCCP08_#1"
    if day < "20140604":
        return "VCCP04", "VCCP04_#1"
    if day == "20260603":
        return "VCCP09", "VCCP09_#11"
    return "VCCP04", "VCCP04_#2"


def form(kind: str, day: str, city: str = "-1", town: str = "-1") -> dict[str, str]:
    if kind == "winners":
        top, menu, statement = "EP", "EPEI01", "EPEI01_#1"
    elif kind == "county":
        top, (menu, statement) = "VC", ("VCCP09", "VCCP09_#11")
    else:
        top, (menu, statement) = "VC", count_page(day)
    return {
        "electionId": "0000000000",
        "requestURI": f"/electioninfo/0000000000/{top.lower()}/{menu.lower()}.jsp",
        "topMenuId": top,
        "secondMenuId": menu,
        "menuId": menu,
        "statementId": statement,
        "oldElectionType": "",
        "electionType": "4",
        "electionName": day,
        "electionCode": CODE,
        "cityCode": city,
        "townCode": town,
        "sggCityCode": "-1",
        "townCodeFromSgg": "-1",
        "sggTownCode": "-1",
    }


def address(kind: str, day: str, city: str = "-1", town: str = "-1") -> str:
    return f"{REPORT}?{urlencode(form(kind, day, city, town))}"


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


def _selector(path: str, **query: str) -> str:
    return f"{SELECT}/{path}?{urlencode({'electionId': '0000000000', **query})}"


def _pairs(url: str) -> dict[str, str]:
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
    return _pairs(_selector("selectbox_cityCodeBySgJson_Old.json", electionCode=day, subElectionCode=CODE))


def towns(day: str, city: str) -> dict[str, str]:
    return _pairs(
        _selector("selectbox_townCodeBySgJson_Old.json", electionCode=day, cityCode=city, subElectionCode=CODE)
    )


def county_page(html: str) -> dict[str, Any]:
    """A province's page by counting office: the options, and per office the figures and the printed percentages."""
    start = html.index('id="table01"')
    head = html[start : html.index("</thead>", start)]
    options = []
    for cell in re.findall(r"<strong>(.*?)</strong>", head, re.DOTALL):
        parts = [part for part in (_plain(part) for part in re.split(r"<br\s*/?>", cell)) if part]
        options.append((parts[0], parts[1]) if len(parts) == 2 else ("", parts[0]))
    width = len(options)
    body = html[html.index("<tbody", start) : html.index("</tbody>", start)]
    found = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
        cells = [_plain(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)]
        if not cells or not cells[0] or "조회된 자료가 없습니다" in cells[0]:
            continue
        if len(cells) < width + 6:
            raise ValueError(f"row {cells} against {width} options")
        figures = [local_dong._number(cell) for cell in cells[1 : width + 6]]
        found.append(
            {
                "labels": [cells[0]],
                "registered": figures[0],
                "ballots": figures[1],
                "votes": figures[2 : 2 + width],
                "valid": figures[2 + width],
                "invalid": figures[3 + width],
            }
        )
    return {"options": options, "rows": found}


def complete(html: str, kind: str = "count") -> bool:
    start = html.find('id="table01"')
    if start < 0 or "<tbody" not in html[start:]:
        return False
    if kind == "winners" or "조회된 자료가 없습니다" in html[start:]:
        return True
    try:
        rows = (county_page(html) if kind == "county" else local_dong.parse_page(html))["rows"]
    except ValueError:
        return False
    return not rows or all(any(row["labels"]) for row in rows)


def _page(job: tuple[str, str, str, str]) -> None:
    kind = job[0]
    url = address(*job)
    if url in raw.keys(RAW):
        return
    for _ in range(30):
        content = _get(REPORT, form(*job))
        if complete(content.decode(errors="replace"), kind):
            raw.store(RAW, url, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
            return
        time.sleep(3)
    raise ValueError(f"{url}: the page keeps coming back without its table or its unit names")


def _attempt(job: tuple[str, str, str, str]) -> str | None:
    try:
        _page(job)
    except (ValueError, httpx.HTTPError) as error:
        return str(error)
    return None


def jobs(day: str) -> list[tuple[str, str, str, str]]:
    found = [("winners", day, "-1", "-1")]
    for city in cities(day):
        found.append(("county", day, city, "-1"))
        if day == "20260603":
            continue
        found += [("count", day, city, town) for town in towns(day, city)]
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


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(url.split("?", 1)[1]).items()}


def _html(url: str) -> str:
    return raw.cached(RAW, url).path.read_text(errors="replace")


def _has_rows(html: str) -> bool:
    start = html.find('id="table01"')
    return start >= 0 and "조회된 자료가 없습니다" not in html[start:]


def _options(page: dict[str, Any]) -> list[tuple[str, str]]:
    return [(first, second) if second else ("", first) for first, second in page["options"]]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text.replace("&nbsp;", " "))).strip()


def winners(day: str) -> list[dict[str, Any]]:
    """Each row of the day's winners' list."""
    html = _html(address("winners", day))
    start = html.index('id="table01"')
    head = html[start : html.index("</thead>", start)]
    names = [_plain(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", head, re.DOTALL)]
    body = html[html.index("<tbody", start) : html.index("</tbody>", start)]
    found = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
        cells = [_plain(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)]
        if len(cells) != len(names):
            continue
        row = dict(zip(names, cells, strict=True))
        votes = re.match(r"[\d,]+", row["득표수 (득표율)"])
        found.append(
            {
                "sgg": row["선거구명"],
                "person": re.sub(r"\s*\(.*\)$", "", row["성명 (한자)"]).replace(" ", ""),
                "votes": int(votes.group().replace(",", "")) if votes else None,
            }
        )
    return found


@dataclass
class Contest:
    ref: str
    sido: str
    title_ko: str
    title_en: str
    option_rows: list[str]
    recs: list[Rec]
    elected: list[bool]
    declared: list[int | None]
    urls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Day:
    day: str
    contests: list[Contest]
    urls: list[str]


CHECKS: Counter[str] = Counter()


def _group(day: str, names: dict[str, str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for city in names:
        groups.setdefault(MERGED if day == "20260603" and city in ("2900", "4600") else local._sido(city), []).append(
            city
        )
    return groups


def _title(sido: str) -> tuple[str, str]:
    if sido == MERGED:
        return f"{local_head.MERGED}교육감", "Superintendent of Education of Jeonnam-Gwangju Special City"
    return f"{nec.SIDO[sido][1]} 교육감", f"Superintendent of Education of {nec.SIDO[sido][2]}"


def _sido_names(sido: str) -> tuple[str, ...]:
    return (local_head.MERGED,) if sido == MERGED else (nec.SIDO[sido][1], nec.SIDO[sido][0])


def contests(day: str) -> Day:
    names = cities(day)
    won = winners(day)
    found: list[Contest] = []
    used: set[int] = set()
    for sido, group in _group(day, names).items():
        rows = [
            (index, row)
            for index, row in enumerate(won)
            if row["sgg"] in _sido_names(sido) or row["sgg"] == names[group[0]]
        ]
        if len(rows) != 1:
            raise ValueError(f"{day} {sido}: {len(rows)} winners {rows}")
        used.update(index for index, _ in rows)
        winner = rows[0][1]
        base = sido[:2]
        urls = [address("winners", day)]
        slate: list[str] | None = None
        recs: list[Rec] = []
        totals: list[dict[str, Any]] = []
        for city in group:
            county = _html(address("county", day, city))
            urls.append(address("county", day, city))
            page = county_page(county)
            options = [person for _, person in _options(page)]
            if slate is None:
                slate = options
            elif slate != options:
                raise ValueError(f"{day} {sido}: candidates differ between {group}")
            total = next((row for row in page["rows"] if row["labels"][0] == "합계"), None)
            if total is None:
                raise ValueError(f"{day} {sido}: no total row in the county page of {city}")
            totals.append(total)
            parts = [row for row in page["rows"] if row["labels"][0] != "합계"]
            if day == "20260603":
                for row in parts:
                    recs.append(
                        Rec(
                            (city[:2] if sido == MERGED else base, nec._town(row["labels"][0])),
                            row["registered"],
                            row["ballots"],
                            row["valid"],
                            row["invalid"],
                            list(row["votes"]),
                        )
                    )
                continue
            before = len(recs)
            for town, town_name in towns(day, city).items():
                url = address("count", day, city, town)
                html = _html(url)
                urls.append(url)
                if not _has_rows(html):
                    CHECKS["empty page"] += 1
                    print(f"{day} {city} {town_name}: no count", flush=True)
                    continue
                parsed = local_dong.parse_page(html)
                if [person for _, person in _options(parsed)] != slate:
                    raise ValueError(f"{url}: candidates {_options(parsed)} differ from {slate}")
                total_row, leaves = local_dong.units(parsed)
                sums = [sum(column) for column in zip(*(leaf["votes"] for leaf in leaves), strict=True)]
                if total_row is not None and sums != total_row["votes"]:
                    CHECKS["page sums differ"] += 1
                    print(f"{day} {city} {town_name}: units sum to {sums}, page to {total_row['votes']}", flush=True)
                for leaf in leaves:
                    channel = leaf["channel"]
                    special = bool(leaf.get("special"))
                    recs.append(
                        Rec(
                            (city[:2] if sido == MERGED else base, nec._town(town_name), leaf["unit"]),
                            leaf["registered"],
                            leaf["ballots"],
                            leaf["valid"],
                            leaf["invalid"],
                            list(leaf["votes"]),
                            channel,
                            "aggregate"
                            if leaf["unit"].startswith("잘못")
                            else SPECIAL.get(channel, "standard")
                            if special
                            else "standard",
                        )
                    )
            if len(recs) == before:
                raise ValueError(f"{day} {city}: no unit counts")
        assert slate is not None
        grand = [sum(column) for column in zip(*(total["votes"] for total in totals), strict=True)]
        leaves_sum = [sum(column) for column in zip(*(rec.votes for rec in recs), strict=True)]
        if leaves_sum != grand:
            CHECKS["province differs"] += 1
            print(f"{day} {sido}: units sum to {leaves_sum}, the county page to {grand}", flush=True)
        chosen = [k for k, person in enumerate(slate) if person == winner["person"]]
        if len(chosen) != 1:
            raise ValueError(f"{day} {sido}: winner {winner['person']} among {slate}")
        elected = [k == chosen[0] for k in range(len(slate))]
        declared: list[int | None] = [winner["votes"] if flag else None for flag in elected]
        notes = [FINEST_NOTE] if day == "20260603" else []
        if winner["votes"] is not None and grand[chosen[0]] != winner["votes"]:
            notes.append(
                f"The winners' list prints {winner['votes']:,} votes for {winner['person']}; the count pages print "
                f"{grand[chosen[0]]:,}, which is the figure stored."
            )
            CHECKS["winner differs"] += 1
            print(f"{day} {sido}: winners' list {winner['votes']}, counts {grand[chosen[0]]}", flush=True)
        korean, english = _title(sido)
        found.append(
            Contest(
                f"edu:{sido}",
                sido,
                f"{day[:4]} {korean}선거",
                f"{day[:4]} {english} election",
                slate,
                recs,
                elected,
                declared,
                urls,
                notes,
            )
        )
    left = [row for index, row in enumerate(won) if index not in used]
    if left:
        raise ValueError(f"{day}: winners' list rows with no count page: {left}")
    return Day(day, found, sorted({url for contest in found for url in contest.urls}))


def days() -> list[Day]:
    return [contests(day) for day in DAYS]


def package_key(day: str) -> str:
    return f"{SOURCE}/{date(int(day[:4]), int(day[4:6]), int(day[6:]))}-education"


def build_registry(every: list[Day]) -> Package:
    p = Package(
        REGISTRY,
        PIPELINE,
        "The offices and electoral system of South Korea's education superintendent elections in the National "
        "Election Commission's election statistics",
    )
    p.external.add(KOREA)
    p.add(
        "source",
        id=COUNT_SOURCE,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=LANDING,
        license=LICENSE,
        license_url=nec.BASE,
        attribution="중앙선거관리위원회 (National Election Commission)",
        notes="개표현황 pages of the election statistics system for the education superintendent elections: one page "
        "per province by city, county, or district, and one page per counting office (구시군위원회) by polling "
        "district or neighborhood.",
    )
    p.add(
        "source",
        id=WINNER_SOURCE,
        kind="official_body",
        publisher=PUBLISHER,
        title=WINNERS_TITLE,
        url=WINNERS_LANDING,
        license=LICENSE,
        license_url=nec.BASE,
        attribution="중앙선거관리위원회 (National Election Commission)",
        notes="당선인명부 pages of the election statistics system for the education superintendent elections.",
    )
    sidos = sorted({contest.sido for day in every for contest in day.contests})
    for sido in sidos:
        korean, english = _title(sido)
        owner = region(sido[:2])
        p.external.add(owner)
        office = key("office", f"edu:{sido}")
        p.add("office", id=office, polity_id=owner, kind="other", body_id=None)
        p.name(office, korean, "ko", "official")
        p.name(office, english, "en", "official", False)
    rules = SYSTEMS["fptp"][0]
    sid = key("system", "fptp")
    p.add("electoral_system", id=sid, polity_id=KOREA, valid_from=None, valid_to=None, rules=rules, rules_version=1)
    p.name(sid, "Superintendents of education, plurality of the valid votes", "en", "official")
    p.add("note", id=key("note", sid), entity_id=sid, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for url in sorted({url for day in every for url in day.urls}):
        p.uses(raw.cached(RAW, url))
    return p


def build_day(day: Day) -> Package:
    held = date(int(day.day[:4]), int(day.day[4:6]), int(day.day[6:]))
    p = Package(
        package_key(day.day),
        PIPELINE,
        f"South Korea's {held} education superintendent elections from the National Election Commission's statistics "
        "system, at the finest unit it prints",
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
        kind="local" if day.day in local.DATES else "by_election",
        date_from=held,
        date_to=held,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{day.day[:4]}년 {held.month}월 {held.day}일 교육감선거", "ko", "official")
    p.name(election, f"{held} South Korean education superintendent elections", "en", "official", False)
    made: dict[tuple[str, ...], str] = {}
    all_recs = [rec for contest in day.contests for rec in contest.recs]
    for contest in day.contests:
        _channels_split(contest.recs)
        _apart(contest.recs)
        _labels(contest.recs)
    _write_units(p, f"edu:{day.day}", all_recs, made)
    for contest in day.contests:
        c = key("contest", day.day, contest.ref)
        p.add(
            "contest",
            id=c,
            election_id=election,
            kind="office",
            office_id=key("office", contest.ref),
            body_id=None,
            magnitude=1,
            round_number=1,
            electoral_system_id=key("system", "fptp"),
            default_result_kind="counted",
        )
        p.name(c, contest.title_ko, "ko", "official")
        p.name(c, contest.title_en, "en", "official", False)
        for note in contest.notes:
            p.add("note", id=key("note", c, note[:40]), entity_id=c, kind="caveat", body_md=note, lang="en")
        rs = key("result_set", day.day, contest.ref)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=c,
            stage="certified",
            kind="counted",
            source_id=COUNT_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=f"{METHOD} {SEAT_NOTE}", lang="en")
        options: list[str] = []
        for number, (person, won, vote) in enumerate(
            zip(contest.option_rows, contest.elected, contest.declared, strict=True), 1
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
