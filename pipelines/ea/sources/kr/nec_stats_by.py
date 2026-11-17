"""South Korea's by-elections and re-runs as `kr.nec_stats` date packages.

`kr.byelections` caches the statistics system's polling-district count pages and winners' lists for every day the
system's 재·보궐선거 menu offers. This module reads them and builds one package per day: the day's by-election, with
every contest held that day, each option and each count at the polling district (with the early, absentee, and
overseas rows the page prints). The parties, offices, bodies, and electoral systems the days use go in one registry
package of their own, so the days build and check without the registry of the general elections.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.kr import byelections, local, local_head, na, nec, referendums
from ea.sources.kr.local_dong import parse_page
from ea.sources.kr.nec import CHANNELS
from ea.sources.kr.nec_stats import (
    KOREA,
    LANDING,
    LICENSE,
    METHOD,
    PUBLISHER,
    REASONS,
    SYSTEMS,
    _apart,
    _channels_split,
    _labels,
    _write_units,
    region,
)

SCHEME_CODES = ("kr.nec_sido", "kr.nec_sigungu", "kr.nec_unit")
from ea.sources.kr.nec_stats import Rec

PIPELINE = "kr.nec_stats_byelections"
SOURCE = "kr.nec_stats"
REGISTRY = f"{SOURCE}/registry-byelections"
SPECIAL = {2: "aggregate", 3: "postal_district", 4: "abroad_mission"}
TITLE = "선거통계시스템: 역대선거 재·보궐선거 개표현황 (투표구별)"
WINNERS_TITLE = "선거통계시스템: 역대선거 재·보궐선거 당선인 명부"
EXISTING_DAYS = {
    *nec.DATES,
    *na.DATES,
    *local.DATES,
    *referendums.NAMES,
    "20080409",
    "20040415",
    "20000413",
}
KINDS = {
    "2": ("body_seats", "na", "국회의원 재·보궐선거", "National Assembly by-election"),
    "3": (
        "office",
        "head",
        "시·도지사 재·보궐선거",
        "governor or mayor of a province or metropolitan city by-election",
    ),
    "4": ("office", "mayor", "구·시·군의 장 재·보궐선거", "city, county, or district head by-election"),
    "5": ("body_seats", "sido-council", "시·도의회의원 재·보궐선거", "metropolitan and provincial council by-election"),
    "6": (
        "body_seats",
        "sigungu-council",
        "구·시·군의회의원 재·보궐선거",
        "city, county, and district council by-election",
    ),
    "11": ("office", "edu", "교육감 재·보궐선거", "education superintendent by-election"),
}
FAMILY = {code: kind[1] for code, kind in KINDS.items()}
SEAT_NOTE = (
    "Each seat is filled by plurality of the valid votes (Public Official Election Act, art. 187–188). A by-election "
    "or re-run fills a seat that fell vacant or whose election was voided."
)
TOTAL_NOTE = (
    "The commission prints a total row for each counting office, and the polling-district, early, absentee, and "
    "overseas rows of the page add up to it."
)


@dataclass
class Page:
    day: str
    code: str
    city: str
    sgg_city: str
    town: str
    sgg_town: str
    url: str
    html: str


@dataclass
class Block:
    """One constituency's rows on a count page."""

    label: str
    total: dict[str, Any] | None = None
    leaves: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Found:
    day: str
    code: str
    city: str
    name: str
    sgg: str
    options: list[tuple[str, str]]
    recs: list[Rec] = field(default_factory=list)
    totals: list[dict[str, Any]] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


def read_pages(html: str) -> list[dict[str, Any]]:
    """Parse a count page. The system sometimes prints the candidate names as body rows (one before each
    constituency's rows) under an empty second header row; each such group parses as a page of its own."""
    empty = re.search(r"<tr>\s*</tr>\s*</thead>", html)
    if empty is None:
        return [parse_page(html)]
    start = html.index("<tbody", empty.end())
    rows = re.findall(r"<tr[^>]*>.*?</tr>", html[start : html.index("</tbody>", start)], re.DOTALL)
    groups: list[tuple[str, list[str]]] = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if any("<strong>" in cell for cell in cells):
            names = [cell for cell in cells if "<strong>" in cell or cell.strip() == "계"]
            groups.append(("<tr>" + "".join(f"<th>{cell}</th>" for cell in names) + "</tr>", []))
        elif groups:
            groups[-1][1].append(row)
    return [
        parse_page(html[: empty.start()] + header + "</thead><tbody>" + "".join(body) + "</tbody>")
        for header, body in groups
    ]


def _section_unit(label: str) -> str:
    """The neighborhood a polling-district name belongs to: 신도림동제1투 is in 신도림동, 주상면투표소 in 주상면."""
    stripped = re.sub(r"(제?\d+투표?구?|투표소.*|제?\d+투표소)$", "", label).strip()
    return stripped or label


def blocks(page: dict[str, Any]) -> list[Block]:
    """The page's rows grouped by constituency, each leaf tagged with its unit, channel, and neighborhood section.

    A polling-district page has the constituency (when the page lists several) and the district; a neighborhood page
    has the constituency, the neighborhood, and the neighborhood's channel (계, 관내사전투표, or 선거일투표)."""
    lead = page["lead"]
    found: list[Block] = []
    current: Block | None = None
    section: list[dict[str, Any]] | None = None
    for row in page["rows"]:
        labels = row["labels"]
        label = labels[0] if lead > 1 else ""
        if lead > 1 and label:
            current = Block(label)
            found.append(current)
            section = None
        elif current is None:
            current = Block("")
            found.append(current)
        if lead == 3:
            unit, part = labels[1], labels[2]
            if unit == "합계":
                current.total = row
                continue
            if part == "계":
                continue
            channel = CHANNELS.get(unit, 0) or {"관내사전투표": 2, "선거일투표": 1}.get(part, 0)
            current.leaves.append({**row, "unit": unit, "channel": channel, "dong": unit if part else None})
            continue
        unit = labels[-1]
        if unit == "계":
            current.total = row
            continue
        if unit == "소계":
            section = []
            continue
        channel = CHANNELS.get(unit, 0)
        leaf = {**row, "unit": unit, "channel": channel, "dong": None}
        if channel and section is not None:
            section.append(leaf)
        elif section is not None and not channel:
            name = _section_unit(unit)
            for early in section:
                early["dong"] = name
            section.clear()
        current.leaves.append(leaf)
    return found


def _options(page: dict[str, Any]) -> list[tuple[str, str]]:
    """(party text, person) for each candidate in the header; education candidates carry no party."""
    options = []
    for first, second in page["options"]:
        options.append((first, second) if second else ("", first))
    return options


def _independent(party: str) -> bool:
    return party in {"무소속", ""}


def _load(day: str) -> list[Page]:
    index = raw._read(byelections.SOURCE)
    pages = []
    for url in sorted(index):
        if "electionInfo_report" not in url or "EPEI01" in url:
            continue
        query = {k: v[0] for k, v in parse_qs(url.split("?", 1)[1]).items()}
        if query["electionName"] != day:
            continue
        if ((day, query["electionCode"]) in byelections.NEIGHBORHOOD) != (query["secondMenuId"] == "VCCP04"):
            continue
        html = (raw.DATA / "raw" / byelections.SOURCE / str(index[url]["file"])).read_text(errors="replace")
        if not byelections.has_rows(html):
            continue
        pages.append(
            Page(
                day,
                query["electionCode"],
                query["cityCode"],
                query["sggCityCode"],
                query["townCode"] if query["townCode"] != "-1" else query["townCodeFromSgg"],
                query["sggTownCode"],
                url,
                html,
            )
        )
    return pages


def _winners(day: str) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Each winners' list row by (code, city, constituency name)."""
    index = raw._read(byelections.SOURCE)
    found: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for url in sorted(index):
        if "EPEI01" not in url:
            continue
        query = {k: v[0] for k, v in parse_qs(url.split("?", 1)[1]).items()}
        if query["electionName"] != day:
            continue
        html = (raw.DATA / "raw" / byelections.SOURCE / str(index[url]["file"])).read_text(errors="replace")
        start = html.find('id="table01"')
        if start < 0 or "<tbody" not in html[start:]:
            continue
        head = html[start : html.index("</thead>", start)]
        names = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()
            for cell in re.findall(r"<th[^>]*>(.*?)</th>", head, re.DOTALL)
        ]
        body = html[html.index("<tbody", start) : html.index("</tbody>", start)]
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
            cells = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell.replace("&nbsp;", " "))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)
            ]
            if len(cells) != len(names):
                continue
            row = dict(zip(names, cells, strict=True))
            person = re.sub(r"\s*\(.*\)$", "", row["성명 (한자)"]).strip()
            votes = re.match(r"[\d,]+", row.get("득표수 (득표율)", ""))
            found[query["electionCode"], query["cityCode"], row.get("선거구명", "")].append(
                {
                    "person": person,
                    "party": row.get("정당명", ""),
                    "votes": int(votes.group().replace(",", "")) if votes else None,
                    "sgg": row.get("선거구명", ""),
                    "town": row.get("구시군명", ""),
                    "url": url,
                }
            )
    return found


def contests(day: str) -> tuple[list[Found], list[Page]]:
    pages = _load(day)
    found: dict[tuple[str, ...], Found] = {}
    for page in pages:
        for parsed in read_pages(page.html):
            options = _options(parsed)
            for block in blocks(parsed):
                if page.code == "2":
                    ident = (page.code, page.city, block.label)
                    name = block.label
                elif page.code in {"3", "11"}:
                    ident = (page.code, page.city)
                    name = ""
                elif page.code == "4":
                    ident = (page.code, page.city, page.sgg_city)
                    name = byelections.sgg_cities(day, page.code, page.city)[page.sgg_city]
                else:
                    ident = (page.code, page.city, page.sgg_town)
                    name = byelections.sgg_towns(day, page.code, page.town).get(page.sgg_town) or block.label
                code_of = {"4": page.sgg_city, "5": page.sgg_town, "6": page.sgg_town}.get(page.code, "")
                entry = found.setdefault(ident, Found(day, page.code, page.city, name, code_of, options))
                if entry.options != options:
                    raise ValueError(f"{page.url}: options {options} differ from {entry.options}")
                if page.url not in entry.urls:
                    entry.urls.append(page.url)
                town = byelections.towns(day, page.code, page.city).get(page.town) or ""
                sido = _sido_name(day, page.code, page.city)
                if block.total is not None:
                    entry.totals.append({**block.total, "town": town})
                for leaf in block.leaves:
                    unit = leaf["unit"]
                    channel = leaf["channel"]
                    if channel and leaf["dong"] is None:
                        path = (sido, town)
                        kind = SPECIAL.get(channel, "standard")
                    elif channel:
                        path = (sido, town, leaf["dong"])
                        kind = SPECIAL.get(channel, "standard")
                    else:
                        path = (sido, town, unit)
                        kind = "aggregate" if unit.startswith("잘못") else "standard"
                    entry.recs.append(
                        Rec(
                            path,
                            leaf["registered"],
                            leaf["ballots"],
                            leaf["valid"],
                            leaf["invalid"],
                            list(leaf["votes"]),
                            channel,
                            kind,
                        )
                    )
    return list(found.values()), pages


def _sido_name(day: str, code: str, city: str) -> str:
    return byelections.cities(day, code)[city]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([PIPELINE, kind, *(str(part) for part in parts)])))


COUNT_SOURCE = key("source", "counts")
WINNER_SOURCE = key("source", "winners")
ASSEMBLY = "assembly"


@dataclass
class Contest:
    ref: str
    kind: str
    title_ko: str
    title_en: str
    option_rows: list[tuple[str, str]]
    system: str
    recs: list[Rec]
    elected: list[bool]
    declared: list[int | None]
    polity: str
    office: tuple[str, str, str, str, str] | None = None
    body: tuple[str, str, str, str] | None = None
    urls: list[str] = field(default_factory=list)
    tacit: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Day:
    day: str
    contests: list[Contest]
    urls: list[str]


def _sido(city: str) -> str:
    return local._sido(city)


def _place(name: str) -> str:
    return re.sub(r"(?:제\d+|[가-힣])선거구$", "", name)


def _match(rows: list[dict[str, Any]], options: list[tuple[str, str]]) -> list[dict[str, Any] | None]:
    """Each option's winners' list row, matched by the candidate's name and then party."""
    found: list[dict[str, Any] | None] = []
    for party, person in options:
        same = [row for row in rows if row["person"] == person]
        if len(same) > 1:
            same = [row for row in same if row["party"] == party] or same
        found.append(same[0] if same else None)
    return found


def _contest(entry: Found, rows: list[dict[str, Any]], day: str) -> Contest:
    kind, family, korean, english = KINDS[entry.code]
    sido = _sido(entry.city)
    sido_ko, sido_en = nec.SIDO[sido][1], nec.SIDO[sido][2]
    matched = _match(rows, entry.options) if entry.options else []
    elected = [row is not None for row in matched]
    declared = [row["votes"] if row is not None else None for row in matched]
    year = day[:4]
    where_ko = sido_ko if entry.code in {"3", "11"} else f"{sido_ko} {entry.name}"
    where_en = sido_en if entry.code in {"3", "11"} else f"{sido_en} {entry.name}"
    ref = ":".join(part for part in (family, entry.city, entry.sgg or entry.name) if part)
    if entry.code == "2":
        ref = f"na:{entry.city}:{entry.name}"
    office = body = None
    polity = "KR"
    if entry.code == "3":
        office_kind, office_ko, office_en = local_head._title(sido)
        office = (f"head:{sido}", f"KR-{sido}", office_kind, office_ko, office_en)
        polity = f"KR-{sido}"
    elif entry.code == "11":
        office = (
            f"edu:{sido}",
            f"KR-{sido}",
            "other",
            f"{sido_ko} 교육감",
            f"Superintendent of Education of {sido_en}",
        )
        polity = f"KR-{sido}"
    elif entry.code == "4":
        office = (
            f"mayor:{entry.city}:{entry.sgg or entry.name}",
            "KR",
            "mayor",
            f"{entry.name}장",
            f"Head of {entry.name}",
        )
    elif entry.code == "2":
        body = (ASSEMBLY, "KR", "국회", "National Assembly")
    elif entry.code == "5":
        body = (f"council:{sido}", f"KR-{sido}", f"{sido_ko}의회", f"{sido_en} council")
    else:
        place = _place(entry.name)
        body = (f"council:{sido}:{place}", "KR", f"{place}의회", f"{place} council")
    magnitude = max(sum(elected), 1)
    return Contest(
        ref,
        kind,
        f"{year} {where_ko} {korean}",
        f"{year} {where_en} {english}",
        entry.options,
        "sntv" if magnitude > 1 else "fptp",
        entry.recs,
        elected,
        declared,
        polity,
        office,
        body,
        entry.urls,
    )


def days() -> list[Day]:
    found = []
    for day in byelections.DATES:
        entries, _ = contests(day)
        winners = _winners(day)
        used: set[tuple[str, str, str]] = set()
        built = []
        for entry in entries:
            if entry.code in {"3", "11"}:
                keys = [k for k in winners if k[0] == entry.code and k[1] == entry.city]
            else:
                keys = [(entry.code, entry.city, entry.name)] if (entry.code, entry.city, entry.name) in winners else []
            used.update(keys)
            rows = [row for k in keys for row in winners[k]]
            if not rows:
                print(f"{day} {entry.code} {entry.city} {entry.name}: no winners' list row", flush=True)
            contest = _contest(entry, rows, day)
            contest.urls.extend(row["url"] for row in rows if row["url"] not in contest.urls)
            built.append(contest)
        for k, rows in winners.items():
            if k in used:
                continue
            code, city, _ = k
            if code not in KINDS:
                continue
            # A winners' list row with no count page: the seat was filled without a vote (무투표당선).
            entry = Found(
                day,
                code,
                city,
                rows[0]["sgg"] or "",
                rows[0]["town"] or "",
                [(row["party"], row["person"]) for row in rows],
            )
            contest = _contest(entry, rows, day)
            contest.elected = [True] * len(rows)
            contest.declared = [None] * len(rows)
            contest.tacit = True
            contest.urls = sorted({row["url"] for row in rows})
            built.append(contest)
        urls = sorted({url for contest in built for url in contest.urls})
        found.append(Day(day, built, urls))
    return found


def package_key(day: str) -> str:
    held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
    return f"{SOURCE}/{held}-by-election" if day in EXISTING_DAYS else f"{SOURCE}/{held}"


def _opt_party(text: str) -> str | None:
    return None if _independent(text) else key("party", text)


def build_registry(every: list[Day]) -> Package:
    p = Package(
        REGISTRY,
        PIPELINE,
        "The parties, bodies, offices, and electoral systems of South Korea's by-elections in the National Election "
        "Commission's election statistics",
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
        notes="개표현황(투표구별) pages of the election statistics system's 재·보궐선거 menu, one page per city, county, or "
        "district counting office and contest.",
    )
    p.add(
        "source",
        id=WINNER_SOURCE,
        kind="official_body",
        publisher=PUBLISHER,
        title=WINNERS_TITLE,
        url=byelections.WINNERS_LANDING,
        license=LICENSE,
        license_url=nec.BASE,
        attribution="중앙선거관리위원회 (National Election Commission)",
        notes="당선인명부 pages of the election statistics system's 재·보궐선거 menu.",
    )
    offices: dict[str, tuple[str, str, str, str, str]] = {}
    bodies: dict[str, tuple[str, str, str, str]] = {}
    systems: set[str] = set()
    parties: set[str] = set()
    urls: set[str] = set()
    for day in every:
        urls.update(day.urls)
        for contest in day.contests:
            systems.add(contest.system)
            if contest.office:
                offices[contest.office[0]] = contest.office
            if contest.body:
                bodies[contest.body[0]] = contest.body
            parties.update(party for party, _ in contest.option_rows if not _independent(party))
    for code, (_, polity, kind, korean, english) in sorted(offices.items()):
        owner = region(polity.removeprefix("KR-")) if polity != "KR" else KOREA
        p.external.add(owner)
        p.add("office", id=key("office", code), polity_id=owner, kind=kind, body_id=None)
        p.name(key("office", code), korean, "ko", "official")
        p.name(key("office", code), english, "en", "official", False)
    for code, (_, polity, korean, english) in sorted(bodies.items()):
        owner = region(polity.removeprefix("KR-")) if polity != "KR" else KOREA
        p.external.add(owner)
        p.add("body", id=key("body", code), polity_id=owner, kind="unicameral" if code == ASSEMBLY else "council")
        p.name(key("body", code), korean, "ko", "official")
        p.name(key("body", code), english, "en", "official", False)
    for code in sorted(systems):
        rules, english, note = SYSTEMS[code]
        sid = key("system", code)
        p.add("electoral_system", id=sid, polity_id=KOREA, valid_from=None, valid_to=None, rules=rules, rules_version=1)
        p.name(sid, english, "en", "official")
        p.add("note", id=key("note", sid), entity_id=sid, kind="methodology", body_md=note, lang="en")
    for text in sorted(parties):
        p.add("party", id=key("party", text), kind="party", polity_id=KOREA)
        p.name(key("party", text), text, "ko", "official")
    for url in sorted(urls):
        p.uses(raw.cached(byelections.SOURCE, url))
    return p


def build_day(day: Day) -> Package:
    held = date(int(day.day[:4]), int(day.day[4:6]), int(day.day[6:]))
    p = Package(
        package_key(day.day),
        PIPELINE,
        f"South Korea's {held} by-elections from the National Election Commission's statistics system, at the "
        "polling district",
        depends=[REGISTRY],
    )
    p.external.add(KOREA)
    p.external.update(SCHEME_CODES)
    for url in day.urls:
        artifact = raw.cached(byelections.SOURCE, url)
        p.artifact(artifact, WINNER_SOURCE if "EPEI01" in url else COUNT_SOURCE)
    election = key("election", day.day)
    p.add(
        "election",
        id=election,
        polity_id=KOREA,
        kind="by_election",
        date_from=held,
        date_to=held,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{day.day[:4]}년 {held.month}월 {held.day}일 재·보궐선거", "ko", "official")
    p.name(election, f"{held} South Korean by-elections", "en", "official", False)
    made: dict[tuple[str, ...], str] = {}
    token = f"by:{day.day}"
    all_recs = [rec for contest in day.contests for rec in contest.recs]
    for contest in day.contests:
        _channels_split(contest.recs)
        _apart(contest.recs)
        _labels(contest.recs)
    _write_units(p, token, all_recs, made)
    for contest in day.contests:
        if not contest.tacit and not contest.recs:
            raise ValueError(f"{day.day} {contest.ref}: a counted contest without a count")
        c = key("contest", day.day, contest.ref)
        p.add(
            "contest",
            id=c,
            election_id=election,
            kind=contest.kind,
            office_id=key("office", contest.office[0]) if contest.office else None,
            body_id=key("body", contest.body[0]) if contest.body else None,
            magnitude=max(sum(contest.elected), 1),
            round_number=1,
            electoral_system_id=key("system", contest.system),
            default_result_kind="tacit" if contest.tacit else "counted",
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
            kind="tacit" if contest.tacit else "counted",
            source_id=WINNER_SOURCE if contest.tacit else COUNT_SOURCE,
            status="active",
            is_default=True,
            completeness=None if contest.tacit else 1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD + " " + SEAT_NOTE, lang="en")
        options: list[str] = []
        for number, ((party, person), won, vote) in enumerate(
            zip(contest.option_rows, contest.elected, contest.declared, strict=True), 1
        ):
            o = key("option", day.day, contest.ref, number)
            options.append(o)
            party_id = _opt_party(party)
            p.add(
                "contest_option",
                id=o,
                contest_id=c,
                kind="candidate",
                party_id=party_id,
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
                member_of_party_id=party_id,
                is_independent=party == "무소속",
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
                if value is None:
                    continue
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
    return [REGISTRY, *(package_key(day) for day in byelections.DATES)]


def fetch() -> None:
    byelections.fetch()


def parse() -> None:
    every = days()
    build_registry(every).write()
    held: Counter[str] = Counter()
    for day in every:
        p = build_day(day)
        for code, count, _ in package.check(p.write()):
            held[code] += count
            if code in REASONS:
                p.accept(code, f"{count:,} {REASONS[code]}")
        if p.accepted:
            p.write()
        print(f"{p.key}: {len(day.contests)} contests, accepted {sorted(p.accepted)}", flush=True)
