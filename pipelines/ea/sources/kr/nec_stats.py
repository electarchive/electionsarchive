"""South Korea's National Election Commission election statistics, as one package per election date.

The adapters read what the older `kr.*` modules parse from the statistics system's report pages (`data/raw/kr.*`
and `data/parsed/kr.*`), and this module builds file-first packages from it: every contest of a date, with each
option, and every count at the finest unit the commission prints.
"""

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.kr import (
    areas,
    local,
    local_dong,
    local_head,
    local_pr,
    local_sgg_pr,
    na,
    na_pr,
    nec,
    referendums,
    sigungu,
    sigungu_head,
)
from ea.store import DATA

SOURCE = "kr.nec_stats"
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "선거통계시스템: 역대선거 개표현황"
LANDING = f"{nec.BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=VC&secondMenuId=VCCP08"
LICENSE = "No license stated: public election statistics of the National Election Commission; facts published with attribution"
SEATS = {"elected": "elected", "lost": "lost"}
SPECIAL = {2: "aggregate", 3: "postal_district", 4: "abroad_mission"}
SCHEMES = {1: "kr.nec_sido", 2: "kr.nec_sigungu"}
LEAF = "kr.nec_unit"
SCHEME_ROWS = (
    ("kr.nec_sido", 1, "administrative", "province or metropolitan city"),
    ("kr.nec_sigungu", 2, "administrative", "city, county, or district"),
    ("kr.nec_constituency", 3, "electoral_district", "constituency"),
    ("kr.nec_sido_council_constituency", 3, "electoral_district", "council constituency"),
    ("kr.nec_sigungu_council_constituency", 3, "electoral_district", "council constituency"),
    ("kr.nec_unit", 4, "precinct", "polling district or neighborhood"),
)
OPTION_KINDS = {"list_allocation": "list", "ballot_question": "answer"}
SKIPPED: Counter[tuple[str, str]] = Counter()
METHOD = (
    "Counted by the National Election Commission and printed in its election statistics system (개표현황). Each "
    "figure is stored as printed for the finest unit the page reports: a polling district or a neighborhood "
    "(읍면동) where the commission prints one, otherwise a city, county, or district. Registered voters, ballots "
    "cast, valid and invalid ballots, and each option's votes are stored where the page prints them. Early "
    "voting, absentee, and overseas ballots are stored on their own channels and units where the page lists them "
    "apart."
)
EARLY_NOTE = (
    "Registered voters and ballots cast are not published for the elections of 1948–1960; the commission prints "
    "them as zero."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


KOREA = str(uuid5(package.NAMESPACE, "polity\x1fKR"))


def region(code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"polity\x1fKR-{code}"))


NEC_SOURCE = key("source", "nec")
REGISTRY = f"{SOURCE}/registry"


@dataclass
class Opt:
    label: str
    party: str | None = None
    person: str | None = None
    independent: bool = False
    seats: int | None = None
    elected: bool = False


@dataclass
class Rec:
    path: tuple[str, ...]
    registered: int | None
    ballots: int | None
    valid: int
    invalid: int
    votes: list[int]
    channel: int = 0
    kind: str = "standard"
    scheme: str | None = None


@dataclass
class Contest:
    ref: str
    kind: str
    title_ko: str
    title_en: str
    system: str
    options: list[Opt]
    recs: list[Rec]
    polity: str = "KR"
    office: str | None = None
    body: str | None = None
    magnitude: int | None = None
    tacit: bool = False
    notes: list[str] = field(default_factory=list)
    declared: list[int | None] | None = None
    artifacts: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Day:
    day: str
    kind: str
    title_ko: str
    title_en: str
    contests: list[Contest] = field(default_factory=list)


SYSTEMS: dict[str, tuple[dict[str, object], str, str]] = {
    "fptp": (
        {"formula": "fptp"},
        "Plurality of the valid votes",
        "The candidate with the most valid votes is elected (Public Official Election Act, art. 187–188).",
    ),
    "sntv": (
        {"formula": "sntv"},
        "Single non-transferable vote in a multi-member constituency",
        "Each voter names one candidate and the candidates with the most valid votes fill the seats.",
    ),
    "referendum": (
        {"formula": "referendum"},
        "Referendum, majority of the valid votes",
        (
            "Voters answer yes or no to one question. The National Election Commission records yes votes, no votes, "
            "invalid ballots, and abstentions; the answer with more valid votes carries."
        ),
    ),
    "list": (
        {
            "formula": "hare",
            "threshold": {"level": "party", "scope": "national", "percent": 3, "constituencies": 5},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        "National Assembly proportional seats, party list ballot",
        (
            "Voters cast a second ballot for a party. Parties with at least 3 percent of the valid list votes or "
            "five constituency seats share the proportional seats by the largest remainder of the Hare quota, and "
            "each party fills its seats in list order (Public Official Election Act, arts. 189 and 200)."
        ),
    ),
    "sido_pr": (
        {
            "formula": "hare",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "percent": 5},
        },
        "Metropolitan and provincial council proportional seats, closed lists with a 5 percent threshold",
        (
            "Each voter casts a separate party-list vote, and the parties with at least 5 percent of the valid votes "
            "share the proportional seats by the largest remainder of the Hare quota."
        ),
    ),
    "sigungu_pr": (
        {
            "formula": "hare",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "percent": 3},
        },
        "City, county, and district council proportional seats, closed lists with a 3 percent threshold",
        (
            "Each voter casts a separate party-list vote, and the parties with at least 3 percent of the valid votes "
            "share the proportional seats by the largest remainder (Public Official Election Act, arts. 190-2 and "
            "190-3)."
        ),
    ),
}


REASONS = {
    "outcomes.seats_magnitude": (
        "contests where the commission's winners' list names a different number of winners than the seats the "
        "constituency elects, such as a tie or a seat declared for a runner-up; stored as declared"
    ),
    "outcomes.single_winner": (
        "single-seat contests where the commission's winners' list names more than one winner, such as a tie "
        "settled by lot; stored as declared"
    ),
    "accounting.votes_valid": (
        "units where the commission prints option votes that do not add up to its printed valid-ballot figure; "
        "stored as printed"
    ),
    "accounting.ballots_sum": (
        "units where the commission prints valid and invalid ballots that do not add up to the ballots cast; "
        "stored as printed"
    ),
    "accounting.turnout": (
        "units where the commission prints more ballots than registered voters, as with voters who cast ballots "
        "away from their roll (absentee, early, overseas, or certificate voting), rolls it prints incompletely, or "
        "rows of misplaced ballots (잘못 투입·구분된 투표지) it prints with no registered voters; stored as printed"
    ),
    "accounting.declared": (
        "contests where the winners' list prints a vote count that differs from the sum of the count pages; "
        "the count pages are stored and the winners' list is kept as the declared figure"
    ),
}


def _channels(recs: list[Rec]) -> None:
    """Units that report several channels keep one channel per row; a plain row beside them is election day."""
    seen: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for rec in recs:
        seen[rec.path].add(rec.channel)
    for rec in recs:
        if rec.channel == 0 and len(seen[rec.path]) > 1:
            rec.channel = 1


CHANNEL_LABELS = {1: "election day", 2: "early voting", 3: "absentee", 4: "overseas", 5: "provisional"}


def _apart(recs: list[Rec]) -> None:
    """A unit holds one row per figure, so a channel beside the first one on the same unit gets its own unit."""
    seen: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for rec in recs:
        if rec.channel not in seen[rec.path]:
            seen[rec.path].append(rec.channel)
    for rec in recs:
        channels = sorted(seen[rec.path])
        if len(channels) > 1 and rec.channel != channels[0]:
            rec.path = (*rec.path[:-1], f"{rec.path[-1]} ({CHANNEL_LABELS[rec.channel]})")


def _labels(recs: list[Rec]) -> None:
    """Rows that repeat a unit and a channel get a counter, as the page lists them in order."""
    count: Counter[tuple[tuple[str, ...], int]] = Counter()
    for rec in recs:
        count[rec.path, rec.channel] += 1
        if count[rec.path, rec.channel] > 1:
            rec.path = (*rec.path[:-1], f"{rec.path[-1]}#{count[rec.path, rec.channel]}")


def _sido(name: str) -> str:
    return nec._sido(name)


def _town(name: str) -> str:
    return nec._town(name)


def _body_id(code: str) -> str:
    return key("body", code)


def _office_id(code: str) -> str:
    return key("office", code)


def _party_id(text: str) -> str:
    return key("party", text)


BODIES = {
    "constituent": ("constituent_assembly", "제헌국회", "Constituent National Assembly"),
    "house": ("lower_house", "민의원", "House of Representatives"),
    "assembly": ("unicameral", "국회", "National Assembly"),
}
OFFICES = {"president": ("head_of_state", "대통령", "President of the Republic of Korea")}
SUB_OFFICES: dict[str, tuple[str, str, str, str]] = {}
SUB_BODIES: dict[str, tuple[str, str, str]] = {}


AREAS_DAY = "20250603"
"""The day whose counting offices name the areas: the 2025 presidential election."""


def build_areas(every: list[Day]) -> areas.Areas:
    found = next(day for day in every if day.day == AREAS_DAY)
    towns = {
        (rec.path[0], rec.path[1])
        for contest in found.contests
        for rec in contest.recs
        if len(rec.path) >= 2 and rec.scheme is None
    }
    return areas.Areas({code: (full, english) for code, (_, full, english) in nec.SIDO.items()}, towns)


def _write_units(
    p: Package, day: str, recs: list[Rec], made: dict[tuple[str, ...], str], places: areas.Areas | None = None
) -> None:
    for rec in recs:
        scheme = rec.scheme or (LEAF if len(rec.path) >= 3 else SCHEMES[len(rec.path)])
        for depth in range(1, len(rec.path) + 1):
            path = rec.path[:depth]
            last = depth == len(rec.path)
            leaf_scheme = scheme if last else (LEAF if depth >= 3 else SCHEMES[depth])
            ident = (leaf_scheme, *path)
            if ident in made:
                continue
            code = f"{day}:" + "/".join(path)
            unit = made[ident] = key("unit", day, leaf_scheme, *path)
            parent = made.get((SCHEMES.get(depth - 1, LEAF), *path[: depth - 1])) if depth > 1 else None
            relation, area_id = "unlocated", None
            if places and leaf_scheme == SCHEMES.get(depth) and (not last or rec.kind == "standard"):
                relation, area_id = places.link(day, path)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=KOREA,
                scheme_code=leaf_scheme,
                code=code,
                parent_id=parent,
                area_relation=relation,
                area_id=area_id,
                kind=rec.kind if last else "standard",
            )
            p.name(unit, path[-1], "ko", "official")


def _unit(made: dict[tuple[str, ...], str], rec: Rec) -> str:
    scheme = rec.scheme or (LEAF if len(rec.path) >= 3 else SCHEMES[len(rec.path)])
    return made[scheme, *rec.path]


def build_day(day: Day, files: list[raw.Artifact], places: areas.Areas) -> Package:
    held = date(int(day.day[:4]), int(day.day[4:6]), int(day.day[6:]))
    p = Package(
        f"{SOURCE}/{held}",
        SOURCE,
        f"South Korea's {held} elections from the National Election Commission's statistics system, at the finest unit "
        "it prints",
        depends=[REGISTRY, areas.GEO],
    )
    p.external.add(KOREA)
    for artifact in files:
        p.artifact(artifact, NEC_SOURCE)
    election = key("election", day.day)
    p.add(
        "election",
        id=election,
        polity_id=KOREA,
        kind=day.kind,
        date_from=held,
        date_to=held,
        date_precision="day",
        status="held",
    )
    p.name(election, day.title_ko, "ko", "official")
    p.name(election, day.title_en, "en", "official", False)
    made: dict[tuple[str, ...], str] = {}
    for contest in day.contests:
        if not contest.recs:
            SKIPPED[day.day, contest.ref.split(":")[0]] += 1
    day.contests = [contest for contest in day.contests if contest.recs]
    all_recs = [rec for contest in day.contests for rec in contest.recs]
    for contest in day.contests:
        _channels_split(contest.recs)
        _apart(contest.recs)
        _labels(contest.recs)
    _write_units(p, day.day, all_recs, made, places)
    overs: Counter[str] = Counter()
    for contest in day.contests:
        c = key("contest", day.day, contest.ref)
        p.add(
            "contest",
            id=c,
            election_id=election,
            kind=contest.kind,
            office_id=_office_id(contest.office) if contest.office else None,
            body_id=_body_id(contest.body) if contest.body else None,
            magnitude=contest.magnitude,
            round_number=1,
            electoral_system_id=key("system", contest.system) if contest.system else None,
            default_result_kind="tacit" if contest.tacit else "counted",
        )
        if contest.kind == "ballot_question":
            p.add("ballot_question", contest_id=c, type="mandatory_referendum")
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
            source_id=NEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        options: list[str] = []
        for number, opt in enumerate(contest.options, 1):
            o = key("option", day.day, contest.ref, number)
            options.append(o)
            party = _party_id(opt.party) if opt.party and not opt.independent else None
            p.add(
                "contest_option",
                id=o,
                contest_id=c,
                kind=OPTION_KINDS.get(contest.kind, "candidate"),
                party_id=party,
                ballot_position=number,
                ballot_label=opt.label,
            )
            if contest.kind in {"office", "body_seats"}:
                person = key("person", day.day, contest.ref, number)
                p.add("person", id=person)
                p.name(person, opt.person or opt.label, "ko", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", day.day, contest.ref, number),
                    person_id=person,
                    option_id=o,
                    role="head",
                    ballot_name=opt.person or opt.label,
                    member_of_party_id=party,
                    is_independent=opt.independent,
                )
            if contest.kind == "ballot_question":
                p.add("outcome", result_set_id=rs, option_id=o, kind="won" if opt.elected else "lost", seats=None)
            elif opt.elected or opt.seats:
                p.add("outcome", result_set_id=rs, option_id=o, kind="elected", seats=opt.seats or 1)
            elif contest.kind != "list_allocation" or contest.options:
                p.add("outcome", result_set_id=rs, option_id=o, kind="lost", seats=None)
        if contest.declared is not None:
            for o, value in zip(options, contest.declared, strict=True):
                if value is not None:
                    p.add("declared", result_set_id=rs, option_id=o, measure=1, value=value)
        for rec in contest.recs:
            if rec.valid > (rec.ballots or rec.valid):
                overs[rs] += 1
            missing = rec.registered == 0 and rec.ballots == 0
            figures = (
                *(() if missing else ((2, None, rec.registered), (3, None, rec.ballots))),
                (4, None, rec.valid),
                (5, None, rec.invalid),
                *((1, options[k], v) for k, v in enumerate(rec.votes)),
            )
            unit = _unit(made, rec)
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


def _channels_split(recs: list[Rec]) -> None:
    """When a contest lists early, absentee, or overseas ballots, its plain rows are election day."""
    if any(rec.channel >= 2 for rec in recs):
        for rec in recs:
            if rec.channel == 0:
                rec.channel = 1


def build_registry(days: list[Day], files: list[raw.Artifact]) -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "South Korea's parties, bodies, offices, and electoral systems in the National Election Commission's "
        "election statistics",
    )
    p.external.add(KOREA)
    p.add(
        "source",
        id=NEC_SOURCE,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=LANDING,
        license=LICENSE,
        license_url=nec.BASE,
        attribution="중앙선거관리위원회 (National Election Commission)",
        notes="개표현황 pages of the election statistics system, one page per city, county, or district counting "
        "office, with the winners' lists.",
    )
    for code, rank, kind, noun in SCHEME_ROWS:
        p.add("scheme", code=code, polity_id=KOREA, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, (kind, korean, english) in BODIES.items():
        p.add("body", id=_body_id(code), polity_id=KOREA, kind=kind)
        p.name(_body_id(code), korean, "ko", "official")
        p.name(_body_id(code), english, "en", "official", False)
    for code, (kind, korean, english) in OFFICES.items():
        p.add("office", id=_office_id(code), polity_id=KOREA, kind=kind, body_id=None)
        p.name(_office_id(code), korean, "ko", "official")
        p.name(_office_id(code), english, "en", "official", False)
    offices: dict[str, tuple[str, str, str, str]] = {}
    bodies: set[str] = set()
    parties: set[str] = set()
    used: set[str] = set()
    for day in days:
        for contest in day.contests:
            if contest.system:
                used.add(contest.system)
            if contest.body and contest.body not in BODIES:
                bodies.add(contest.body)
            if contest.office and contest.office not in OFFICES:
                offices[contest.office] = SUB_OFFICES[contest.office]
            for opt in contest.options:
                if opt.party and not opt.independent:
                    parties.add(opt.party)
    for code, (polity, kind, korean, english) in sorted(offices.items()):
        p.add(
            "office",
            id=_office_id(code),
            polity_id=region(polity.removeprefix("KR-")) if polity != "KR" else KOREA,
            kind=kind,
            body_id=None,
        )
        p.name(_office_id(code), korean, "ko", "official")
        p.name(_office_id(code), english, "en", "official", False)
        if polity != "KR":
            p.external.add(region(polity.removeprefix("KR-")))
    for code in sorted(bodies):
        polity, korean, english = SUB_BODIES[code]
        owner = region(polity.removeprefix("KR-")) if polity != "KR" else KOREA
        p.external.add(owner)
        p.add("body", id=_body_id(code), polity_id=owner, kind="council")
        p.name(_body_id(code), korean, "ko", "official")
        p.name(_body_id(code), english, "en", "official", False)
    for code in sorted(used):
        rules, english, note = SYSTEMS[code]
        sid = key("system", code)
        p.add("electoral_system", id=sid, polity_id=KOREA, valid_from=None, valid_to=None, rules=rules, rules_version=1)
        p.name(sid, english, "en", "official")
        p.add("note", id=key("note", sid), entity_id=sid, kind="methodology", body_md=note, lang="en")
    for text in sorted(parties):
        p.add("party", id=_party_id(text), kind="party", polity_id=KOREA)
        p.name(_party_id(text), text, "ko", "official")
    for artifact in files:
        p.uses(artifact)
    return p


def _channel(value: int) -> int:
    return max(value, 0)


def _files(day: str) -> list[raw.Artifact]:
    found = [raw.cached("kr.referendums", referendums.KEY)] if day in referendums.NAMES else []
    for source in (
        "kr.nec",
        "kr.na",
        "kr.na_pr",
        "kr.local",
        "kr.local_head",
        "kr.local_pr",
        "kr.local_sgg_pr",
        "kr.sigungu",
        "kr.sigungu_head",
        "kr.local_dong",
    ):
        for address in sorted(raw.keys(source)):
            if f"electionName={day}" in address or f"electionCode={day}" in address:
                found.append(raw.cached(source, address))
    seen: set[str] = set()
    unique = []
    for artifact in found:
        if artifact.storage_key not in seen:
            seen.add(artifact.storage_key)
            unique.append(artifact)
    return unique


def _split(text: str) -> tuple[str | None, bool]:
    return (None, True) if text == "무소속" else (text, False)


def presidential(parsed: pl.DataFrame, tickets: pl.DataFrame, day: str) -> Contest:
    rows = parsed.filter(pl.col("day") == day)
    slate = tickets.filter(pl.col("day") == day).sort("ticket")
    winners = dict(nec._winners(day))
    totals = [sum(column) for column in zip(*rows["votes"].to_list(), strict=True)]
    options = []
    declared: list[int | None] = []
    for (party, person), total in zip(slate.select("party", "name").rows(), totals, strict=True):
        text, independent = _split(party)
        options.append(Opt(person, text, person, independent, elected=person in winners))
        declared.append(winners.get(person) if person in winners else None)
    recs = []
    for r in rows.iter_rows(named=True):
        code = _sido(r["sido"])
        path = (code, _town(r["town"]))
        if r["unit"]:
            path = (*path, *([r["dong"]] if r["dong"] else []), r["unit"])
        channel = _channel(r["channel"])
        recs.append(
            Rec(
                path,
                r["registered"],
                r["ballots"],
                r["valid"],
                r["invalid"],
                list(r["votes"]),
                channel,
                SPECIAL.get(channel, "standard") if r["unit"] else "standard",
            )
        )
    ordinal = nec.ORDINALS[day]
    held = f"{day[:4]}"
    return Contest(
        "president",
        "office",
        f"제{ordinal}대 대통령선거",
        f"{held} South Korean presidential election",
        "fptp",
        options,
        recs,
        office="president",
        magnitude=1,
        declared=declared,
        notes=[nec.NOTES[day]] if day in nec.NOTES else [],
    )


def na_constituencies(day: str, results: pl.DataFrame, tickets: pl.DataFrame, winners: pl.DataFrame) -> list[Contest]:
    contests = []
    rows = results.filter(pl.col("day") == day)
    slate = tickets.filter(pl.col("day") == day)
    elected = winners.filter(pl.col("day") == day)
    body = "constituent" if day == na.DATES[0] else "house" if day == "19600729" else "assembly"
    number = na.DATES.index(day) + 1
    for (sido, sgg), group in rows.group_by(["sido", "sgg"], maintain_order=True):
        won = elected.filter((pl.col("sido") == sido) & (pl.col("sgg") == sgg))
        names = slate.filter((pl.col("sido") == sido) & (pl.col("sgg") == sgg)).sort("ticket")
        options = []
        declared: list[int | None] = []
        for party, person in names.select("party", "name").rows():
            text, independent = _split(party)
            victor = won.filter((pl.col("name") == person) & (pl.col("party") == party))
            options.append(Opt(person, text, person, independent, elected=victor.height > 0))
            declared.append(int(victor["votes"][0]) if victor.height and victor["votes"][0] is not None else None)
        recs = []
        for r in group.iter_rows(named=True):
            channel = r["channel"]
            if r["kind"] == "constituency":
                path, scheme, kind = (sido, sgg), "kr.nec_constituency", "standard"
            elif r["kind"] == "part":
                path, scheme, kind = (sido, _town(r["town"])), None, "standard"
            else:
                path, scheme, kind = (sido, _town(r["town"]), r["label"]), None, "standard"
                if r["kind"] == "special":
                    kind = SPECIAL.get(channel, "standard")
            recs.append(
                Rec(
                    path,
                    r["registered"],
                    r["ballots"],
                    r["valid"],
                    r["invalid"],
                    list(r["votes"]),
                    channel,
                    kind,
                    scheme,
                )
            )
        magnitude = max(won.height, 1)
        contests.append(
            Contest(
                f"na:{sido}:{sgg}",
                "body_seats",
                f"{sgg}",
                f"{day[:4]} National Assembly election, {sgg}",
                "sntv" if magnitude > 1 else "fptp",
                options,
                recs,
                body=body,
                magnitude=magnitude,
                declared=declared,
                notes=[EARLY_NOTE] if number == 1 else [],
            )
        )
    return contests


def _office(code: str, polity: str, kind: str, korean: str, english: str) -> str:
    SUB_OFFICES[code] = (polity, kind, korean, english)
    return code


def _sgg_contests(
    day: str,
    family: str,
    results: pl.DataFrame,
    tickets: pl.DataFrame,
    winners: pl.DataFrame,
    scheme: str,
    title: tuple[str, str],
    body_of: Callable[[str, str, str], str],
    by_town: bool = False,
) -> list[Contest]:
    """One contest per constituency of a council election, counted at the constituency."""
    contests = []
    rows = results.filter(pl.col("day") == day)
    rows = rows if by_town else rows.filter(pl.col("town") == "합계")
    slate = tickets.filter(pl.col("day") == day)
    elected = winners.filter(pl.col("day") == day)
    held = day[:4]
    keys = ["city", "town", "sgg"] if by_town else ["city", "sgg"]
    for found_key, won in elected.group_by(keys, maintain_order=True):
        city, sgg = found_key[0], found_key[-1]
        town = found_key[1] if by_town else ""
        where = (pl.col("city") == city) & (pl.col("sgg") == sgg) & ((pl.col("town") == town) if by_town else True)
        sido = _sido_of(city)
        names = slate.filter(where).sort("ticket")
        found = rows.filter(where)
        tacit = found.is_empty()
        options, declared = [], []
        candidates = names.select("party", "name").rows() or won.select("party", "name").rows()
        for party, person in candidates:
            text, independent = _split(party)
            victor = won.filter((pl.col("name") == person) & (pl.col("party") == party))
            options.append(Opt(person, text, person, independent, elected=victor.height > 0))
            vote = victor["votes"][0] if victor.height else None
            declared.append(None if vote is None else int(vote))
        recs = []
        if not tacit:
            r = found.row(0, named=True)
            recs.append(
                Rec(
                    (sido, sgg),
                    r["registered"],
                    r["ballots"],
                    r["valid"],
                    r["invalid"],
                    list(r["votes"]),
                    0,
                    "standard",
                    scheme,
                )
            )
        magnitude = won.height
        body = body_of(city, town, sgg)
        contests.append(
            Contest(
                f"{family}:{city}:{town}:{sgg}" if by_town else f"{family}:{city}:{sgg}",
                "body_seats",
                f"{sgg}",
                f"{held} {title[1]}, {sgg}",
                "sntv" if magnitude > 1 else "fptp",
                options,
                recs,
                body=body,
                magnitude=magnitude,
                tacit=tacit,
                declared=declared if recs else None,
            )
        )
    return contests


def _body(code: str, polity: str, korean: str, english: str) -> str:
    SUB_BODIES[code] = (polity, korean, english)
    return code


def sido_council_body(city: str, town: str, sgg: str) -> str:
    sido = local._sido(city)
    return _body(f"council:{sido}", f"KR-{sido}", f"{nec.SIDO[sido][1]}의회", f"{nec.SIDO[sido][2]} council")


def sigungu_council_body(city: str, town: str, sgg: str) -> str:
    sido = local._sido(city)
    place = sigungu._municipality(town)
    return _body(f"council:{sido}:{place}", "KR", f"{place}의회", f"{place} council")


def _sido_of(city: str) -> str:
    return local._sido(city)


def governors(day: str, results: pl.DataFrame, tickets: pl.DataFrame, winners: pl.DataFrame) -> list[Contest]:
    rows = results.filter(pl.col("day") == day)
    slate = tickets.filter(pl.col("day") == day)
    elected = winners.filter(pl.col("day") == day)
    groups: dict[str, list[str]] = {}
    for city in rows["city"].unique(maintain_order=True):
        groups.setdefault("29+46" if day == "20260603" and city in ("2900", "4600") else local._sido(city), []).append(
            city
        )
    contests = []
    for sido, cities in groups.items():
        won = elected.filter(pl.col("sido").map_elements(local_head._head_sido, return_dtype=pl.String) == sido)
        names = slate.filter(pl.col("city") == cities[0]).sort("ticket").select("ticket", "party", "name").rows()
        chosen = local_head._elected(won["name"][0], won["party"][0], names)
        options = [
            Opt(person, *_split(party)[:1], person, _split(party)[1], elected=ticket == chosen)
            for ticket, party, person in names
        ]
        parts = rows.filter(pl.col("city").is_in(cities) & (pl.col("town") != "합계"))
        recs = []
        for r in parts.iter_rows(named=True):
            recs.append(
                Rec(
                    (sido[:2] if sido != "29+46" else r["city"][:2], _town(r["town"])),
                    r["registered"],
                    r["ballots"],
                    r["valid"],
                    r["invalid"],
                    list(r["votes"]),
                )
            )
        kind, korean, english = local_head._title(sido)
        code = _office(f"head:{sido}", f"KR-{sido[:2]}", kind, korean, english)
        declared: list[int | None] = [None] * len(options)
        declared[chosen - 1] = int(won["votes"][0]) if won["votes"][0] is not None else None
        contests.append(
            Contest(
                f"head:{sido}",
                "office",
                f"{day[:4]} {korean}선거",
                f"{day[:4]} {english} election",
                "fptp",
                options,
                recs,
                office=code,
                magnitude=1,
                declared=declared,
                polity=f"KR-{sido[:2]}",
                notes=[
                    "Gwangju and South Jeolla merged into the Jeonnam-Gwangju Special City for this election; the commission prints one count page for each former area."
                ]
                if sido == "29+46"
                else [],
            )
        )
    return contests


def mayors(day: str, results: pl.DataFrame, tickets: pl.DataFrame, winners: pl.DataFrame) -> list[Contest]:
    contests = []
    rows = results.filter((pl.col("day") == day) & (pl.col("town") != "합계"))
    slate = tickets.filter(pl.col("day") == day)
    elected = winners.filter(pl.col("day") == day)
    for (city, sgg), won in elected.group_by(["city", "sgg"], maintain_order=True):
        sido = _sido_of(city)
        names = slate.filter((pl.col("city") == city) & (pl.col("sgg") == sgg)).sort("ticket")
        group = rows.filter((pl.col("city") == city) & (pl.col("sgg") == sgg))
        tacit = group.is_empty()
        candidates = names.select("party", "name").rows() or won.select("party", "name").rows()
        options, declared = [], []
        for party, person in candidates:
            text, independent = _split(party)
            victor = won.filter((pl.col("name") == person) & (pl.col("party") == party))
            options.append(Opt(person, text, person, independent, elected=victor.height > 0))
            vote = victor["votes"][0] if victor.height else None
            declared.append(None if vote is None else int(vote))
        recs = [
            Rec((sido, _town(r["town"])), r["registered"], r["ballots"], r["valid"], r["invalid"], list(r["votes"]))
            for r in group.iter_rows(named=True)
        ]
        code = _office(f"mayor:{city}:{sgg}", "KR", "mayor", f"{sgg}장", f"Head of {sgg}")
        contests.append(
            Contest(
                f"mayor:{city}:{sgg}",
                "office",
                f"{day[:4]} {sgg}장선거",
                f"{day[:4]} {sgg} head election",
                "fptp",
                options,
                recs,
                office=code,
                magnitude=1,
                tacit=tacit,
                declared=None if tacit else declared,
            )
        )
    return contests


def na_proportional(day: str, results: pl.DataFrame, members: pl.DataFrame) -> Contest:
    rows = results.filter((pl.col("day") == day) & (pl.col("town") != "합계"))
    seats = dict(members.filter(pl.col("day") == day).group_by("party").len().iter_rows())
    names = rows["parties"][0].to_list()
    options = [Opt(text, text, seats=seats.get(text)) for text in names]
    recs = [
        Rec((r["sido"], _town(r["town"])), r["registered"], r["ballots"], r["valid"], r["invalid"], list(r["votes"]))
        for r in rows.iter_rows(named=True)
    ]
    number = na.DATES.index(day) + 1
    return Contest(
        "na-pr",
        "list_allocation",
        f"제{number}대 국회의원선거 비례대표",
        f"{day[:4]} National Assembly election, proportional seats",
        "list" if day != "20200415" else "",
        options,
        recs,
        body="assembly",
        magnitude=sum(seats.values()),
        notes=[na_pr.LINKED] if day == "20200415" else [],
    )


def sido_proportional(day: str, results: pl.DataFrame, winners: pl.DataFrame) -> list[Contest]:
    rows = results.filter((pl.col("day") == day) & (pl.col("town") != "합계"))
    elected = winners.filter(pl.col("day") == day)
    contests = []
    groups: dict[str, list[str]] = {}
    for city in rows["city"].unique(maintain_order=True):
        groups.setdefault("29+46" if day == "20260603" and city in ("2900", "4600") else local._sido(city), []).append(
            city
        )
    for sido, cities in groups.items():
        won = elected.filter(pl.col("sido").map_elements(local_head._head_sido, return_dtype=pl.String) == sido)
        part = rows.filter(pl.col("city").is_in(cities))
        names = part["parties"][0].to_list()
        seats = dict(won.group_by("party").len().iter_rows())
        options = [Opt(text, text, seats=seats.get(text)) for text in names]
        base = sido[:2]
        recs = [
            Rec(
                (base if sido != "29+46" else r["city"][:2], _town(r["town"])),
                r["registered"],
                r["ballots"],
                r["valid"],
                r["invalid"],
                list(r["votes"]),
            )
            for r in part.iter_rows(named=True)
        ]
        label = nec.SIDO[base][1] if sido != "29+46" else local_head.MERGED
        body = _body(f"council:{base}", f"KR-{base}", f"{nec.SIDO[base][1]}의회", f"{nec.SIDO[base][2]} council")
        contests.append(
            Contest(
                f"sido-pr:{sido}",
                "list_allocation",
                f"{day[:4]} {label}의회의원선거 비례대표",
                f"{day[:4]} {nec.SIDO[base][2]} council election, proportional seats",
                "sido_pr",
                options,
                recs,
                body=body,
                magnitude=sum(seats.values()),
            )
        )
    return contests


def sigungu_proportional(day: str, results: list[dict[str, Any]], winners: pl.DataFrame) -> list[Contest]:
    contests = []
    elected = winners.filter(pl.col("day") == day)
    for found in results:
        if found["day"] != day or not found["parts"]:
            continue
        city, name = found["city"], found["contest"]
        sido = local._sido(city)
        seats = dict(
            elected.filter((pl.col("city") == city) & (pl.col("contest") == name)).group_by("party").len().iter_rows()
        )
        names = found["parts"][0]["parties"]
        options = [Opt(text, text, seats=seats.get(text)) for text in names]
        recs = [
            Rec(
                (sido, _town(part["town"])),
                part["registered"],
                part["ballots"],
                part["valid"],
                part["invalid"],
                list(part["votes"]),
            )
            for part in found["parts"]
        ]
        body = sigungu_council_body(city, name, "")
        contests.append(
            Contest(
                f"sigungu-pr:{city}:{name}",
                "list_allocation",
                f"{day[:4]} {name}의회의원선거 비례대표",
                f"{day[:4]} {name} council election, proportional seats",
                "sigungu_pr",
                options,
                recs,
                body=body,
                magnitude=sum(seats.values()),
            )
        )
    return contests


def referendum(day: str, results: pl.DataFrame) -> Contest:
    rows = results.filter((pl.col("day") == day) & (pl.col("sido") != "합계"))
    total = results.filter((pl.col("day") == day) & (pl.col("sido") == "합계")).row(0, named=True)
    carried = total["yes"] > total["no"]
    options = [Opt("찬성", elected=carried), Opt("반대", elected=not carried)]
    recs = [
        Rec((_sido(r["sido"]),), r["registered"], r["ballots"], r["valid"], r["invalid"], [r["yes"], r["no"]])
        for r in rows.iter_rows(named=True)
    ]
    held = referendums.NAMES[day]
    return Contest(
        "referendum",
        "ballot_question",
        f"{day[:4]}년 국민투표",
        held,
        "referendum",
        options,
        recs,
        magnitude=None,
        declared=[total["yes"], total["no"]],
    )


def _dong_ref(page: dict[str, Any]) -> str:
    code, city = page["code"], page["city"]
    if code == "3":
        return f"head:{local._sido(city)}"
    if code == "8":
        return f"sido-pr:{local._sido(city)}"
    if code == "4":
        return f"mayor:{city}:{page['sgg_name']}"
    if code == "9":
        return f"sigungu-pr:{city}:{page['sgg_name']}"
    if code == "5":
        return f"sido-council:{city}:{page['sgg_name']}"
    return f"sigungu-council:{city}:{page['town_name']}:{page['sgg_name']}"


def _order(page: dict[str, Any], contest: Contest) -> list[int] | None:
    """Each contest option's column on the page, matched by the label the page prints."""
    labels = [(person or party).replace(" ", "") for party, person in page["options"]]
    wanted = [opt.label.replace(" ", "") for opt in contest.options]
    columns = [k for k, label in enumerate(labels) if label]
    printed = [labels[k] for k in columns]
    if printed == wanted:
        return columns
    if sorted(printed) == sorted(wanted) and len(set(wanted)) == len(wanted):
        return [columns[printed.index(label)] for label in wanted]
    return None


DONG: Counter[str] = Counter()


def refine(day: Day, pages: list[dict[str, Any]]) -> None:
    """Replace each local contest's counts with the neighborhood or polling-district counts where the commission
    prints them for every counting office of the contest and the page's options match the contest's."""
    by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        if page["day"] == day.day:
            by_ref[_dong_ref(page)].append(page)
    for contest in day.contests:
        found = by_ref.pop(contest.ref, [])
        if not found or contest.tacit:
            DONG["kept"] += 1
            continue
        if not all(page["leaves"] for page in found):
            DONG["empty page"] += 1
            continue
        orders = [_order(page, contest) for page in found]
        if any(order is None for order in orders):
            DONG["options differ"] += 1
            print(
                f"{day.day} {contest.ref}: options {found[0]['options']} against {[o.label for o in contest.options]}"
            )
            continue
        recs: list[Rec] = []
        for page, order in zip(found, orders, strict=True):
            assert order is not None
            base = local._sido(page["city"])[:2]
            town = _town(page["town_name"])
            for leaf in page["leaves"]:
                extra = [v for k, v in enumerate(leaf["votes"]) if k not in order]
                if any(extra):
                    raise ValueError(f"{day.day} {contest.ref}: votes outside the options in {leaf}")
                channel = leaf["channel"]
                recs.append(
                    Rec(
                        (base, town, leaf["unit"]),
                        leaf["registered"],
                        leaf["ballots"],
                        leaf["valid"],
                        leaf["invalid"],
                        [leaf["votes"][k] for k in order],
                        channel,
                        "aggregate"
                        if leaf["unit"].startswith("잘못")
                        else SPECIAL.get(channel, "standard")
                        if leaf["special"]
                        else "standard",
                    )
                )
        old = [sum(column) for column in zip(*(rec.votes for rec in contest.recs), strict=True)]
        new = [sum(column) for column in zip(*(rec.votes for rec in recs), strict=True)]
        if contest.recs and old != new:
            DONG["totals differ"] += 1
            print(f"{day.day} {contest.ref}: units sum to {new}, the count page to {old}")
        contest.recs = recs
        DONG["refined"] += 1
    for ref, left in by_ref.items():
        if any(page["leaves"] for page in left):
            DONG["page without contest"] += 1
            print(f"{day.day} {ref}: no contest")


def _dong_pages() -> list[dict[str, Any]]:
    path = DATA / "parsed" / local_dong.SOURCE / "pages.json"
    return json.loads(path.read_text()) if path.exists() else []


FETCHERS = (
    referendums,
    nec,
    na,
    na_pr,
    local,
    local_head,
    local_pr,
    local_sgg_pr,
    sigungu,
    sigungu_head,
    local_dong,
)


def fetch() -> None:
    for module in FETCHERS:
        module.fetch()
    areas.fetch()


def _table(source: str, name: str) -> pl.DataFrame:
    return pl.read_parquet(DATA / "parsed" / source / f"{name}.parquet")


def days(local_only: bool = False) -> list[Day]:
    found: dict[str, Day] = {}

    def add(day: str, kind: str, korean: str, english: str) -> Day:
        if day in found:
            raise ValueError(f"{day} is held twice")
        found[day] = Day(day, kind, korean, english)
        return found[day]

    if not local_only:
        _national(add)
    _local(add)
    return [found[day] for day in sorted(found)]


def _national(add: Callable[[str, str, str, str], Day]) -> None:
    pres = _table("kr.nec", "results"), _table("kr.nec", "tickets")
    for day in nec.DATES:
        entry = add(
            day,
            "special" if day in {"20170509", "20250603"} else "general",
            f"제{nec.ORDINALS[day]}대 대통령선거",
            f"{day[:4]} South Korean presidential election",
        )
        entry.contests.append(presidential(*pres, day))
    results, tickets, winners = (_table("kr.na", n) for n in ("results", "tickets", "winners"))
    pr_results, pr_members = _table("kr.na_pr", "results"), _table("kr.na_pr", "members")
    for number, day in enumerate(na.DATES, 1):
        entry = add(day, "general", f"제{number}대 국회의원선거", f"{day[:4]} South Korean legislative election")
        entry.contests.extend(na_constituencies(day, results, tickets, winners))
        if day in pr_results["day"].unique().to_list():
            entry.contests.append(na_proportional(day, pr_results, pr_members))
    ref = _table("kr.referendums", "results")
    for day in sorted(referendums.NAMES):
        entry = add(day, "referendum", referendums.NAMES[day], referendums.NAMES[day])
        entry.contests.append(referendum(day, ref))


def _local(add: Callable[[str, str, str, str], Day]) -> None:
    council = [_table("kr.local", n) for n in ("results", "tickets", "winners")]
    head = tuple(_table("kr.local_head", n) for n in ("results", "tickets", "winners"))
    local_pr_data = _table("kr.local_pr", "results"), _table("kr.local_pr", "winners")
    sgg_pr_results = json.loads((DATA / "parsed" / "kr.local_sgg_pr" / "results.json").read_text())
    sgg_pr_winners = _table("kr.local_sgg_pr", "winners")
    sgg = tuple(_table("kr.sigungu", n) for n in ("results", "tickets", "winners"))
    mayor = tuple(_table("kr.sigungu_head", n) for n in ("results", "tickets", "winners"))
    dong = _dong_pages()
    for number, day in enumerate(local.DATES, 1):
        entry = add(day, "local", f"제{number}회 전국동시지방선거", f"{day[:4]} South Korean local elections")
        entry.contests.extend(governors(day, *head))
        entry.contests.extend(mayors(day, *mayor))
        entry.contests.extend(
            _sgg_contests(
                day,
                "sido-council",
                council[0],
                council[1],
                council[2],
                "kr.nec_sido_council_constituency",
                ("시·도의회의원선거", "metropolitan and provincial council election"),
                sido_council_body,
            )
        )
        entry.contests.extend(
            _sgg_contests(
                day,
                "sigungu-council",
                sgg[0],
                sgg[1],
                sgg[2],
                "kr.nec_sigungu_council_constituency",
                ("구·시·군의회의원선거", "city, county, and district council election"),
                sigungu_council_body,
                by_town=True,
            )
        )
        if day in local_pr_data[0]["day"].unique().to_list():
            entry.contests.extend(sido_proportional(day, *local_pr_data))
        entry.contests.extend(sigungu_proportional(day, sgg_pr_results, sgg_pr_winners))
        refine(entry, dong)


def packages() -> list[str]:
    return [REGISTRY, areas.GEO, *(f"{SOURCE}/{date(int(d.day[:4]), int(d.day[4:6]), int(d.day[6:]))}" for d in days())]


def parse() -> None:
    build(days(), registry=True)


def parse_local() -> None:
    """Rebuild the local election days alone. The registry this writes holds only what the local days use, so it
    serves `ea package check` here and is not a release of the registry, which `parse` builds from every day."""
    build(days(local_only=True), registry=True)
    print(dict(DONG), flush=True)


def build(every: list[Day], registry: bool) -> None:
    """Write the geo package, the registry when asked, and each day's package."""
    files = {day.day: _files(day.day) for day in every}
    if registry:
        build_registry(every, [artifact for found in files.values() for artifact in found]).write()
    places = build_areas(every) if any(day.day == AREAS_DAY for day in every) else _stored_areas()
    geo, missing = areas.build(places)
    geo.write()
    if missing:
        print(f"no outline for {', '.join(missing)}", flush=True)
    for day in every:
        p = build_day(day, files[day.day], places)
        for code, count, _ in package.check(p.write()):
            if code in REASONS:
                p.accept(code, f"{count:,} {REASONS[code]}")
        if p.accepted:
            p.write()


def _stored_areas() -> areas.Areas:
    """The areas of the 2025 presidential election, read from the parsed counts when that day is not being built."""
    return build_areas([day for day in days() if day.day == AREAS_DAY])


def emit() -> None:
    package.run("load", packages())
