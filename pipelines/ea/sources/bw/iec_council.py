"""Botswana's council ward results for the 2019 general election, from the annexure of the Independent Electoral
Commission's report to the Minister, by polling district (the wards of each council)."""

import functools
import re
import subprocess
from datetime import date
from typing import NamedTuple
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bw import iec

SOURCE = "bw.iec_council"
BOTSWANA = iec.BOTSWANA
COUNTRY = iec.COUNTRY
REPORT = "https://www.iec.gov.bw/images/Botswana_2019_General_Elections_REPORT(1).pdf"
MAIN = date(2019, 10, 23)
COUNTERMANDED = date(2019, 11, 23)
FIRST_PAGE = "Annexure 2"
LAST_PAGE = "Annexure 3"

# The annexure prints no council names. Its ward numbers run through each council in the order of the 2009 report,
# and the wards the annexure names match the councils of the earlier reports through these ranges. Chobe became a
# district council of its own, out of the first seven wards of North West.
COUNCILS = (
    (1, 7, "CHOBE DISTRICT COUNCIL"),
    (8, 46, "NORTH WEST DISTRICT COUNCIL"),
    (47, 65, "NORTH EAST DISTRICT COUNCIL"),
    (66, 84, "FRANCISTOWN CITY COUNCIL"),
    (85, 138, "CENTRAL DISTRICT COUNCIL"),
    (139, 152, "SELIBE PHIKWE TOWN COUNCIL"),
    (153, 238, "CENTRAL DISTRICT COUNCIL"),
    (239, 261, "KGATLENG DISTRICT COUNCIL"),
    (262, 291, "GABORONE CITY COUNCIL"),
    (292, 311, "SOUTH EAST DISTRICT COUNCIL"),
    (312, 377, "KWENENG DISTRICT COUNCIL"),
    (378, 389, "LOBATSE TOWN COUNCIL"),
    (390, 441, "SOUTHERN DISTRICT COUNCIL"),
    (442, 448, "JWANENG TOWN COUNCIL"),
    (449, 470, "KGALAGADI DISTRICT COUNCIL"),
    (471, 490, "GHANZI DISTRICT COUNCIL"),
)
# The report's text counts the seats won: 334 for the BDP including the countermanded ward, 129 for the UDC, and
# 17, 6, and 1 for the BPF, AP, and BMD, with four independents, one of them in Otse-Kodibeleng's January 2020 fresh
# election, which the annexure does not carry. The wards' leaders agree with every count but the BDP's, which they
# put at 333; the text's seats add to 491 for 490 wards.
SEATS = {"BDP": 333, "UDC": 129, "BPF": 17, "AP": 6, "BMD": 1, "IND": 3}
TIED = {"197"}
COUNTERMANDED_WARDS = {"179"}
PARTIES = {
    "AP": "Alliance for Progressives",
    "BDP": "Botswana Democratic Party",
    "BMD": "Botswana Movement for Democracy",
    "BPF": "Botswana Patriotic Front",
    "UDC": "Umbrella for Democratic Change",
}
COALITIONS = {"UDC"}

HEADER = re.compile(r"^(\d{1,3}) (\S.*?) ((?:\d+ ){2,4})(\d+)% (-?\d+)$")
STARRED = re.compile(r"^\*\s+")
CANDIDATE = re.compile(r"^([A-Za-z][A-Za-z.,'’\- ]*?) ([MF]) ([A-Za-z]+) (\d+)(?: \d+)?$")


SPLIT = re.compile(r"^([MF]) ([A-Za-z]+) (\d+)$")
NAMES = re.compile(r"^[A-Z][A-Z.,'’\- ]*$")
NOISE = {"REPORT", "CODE", "NAME"}


MARGINS: list[str] = []
# Lesunyaneng prints one candidate's party as BBDP, and the BDP contested all 490 wards only with that read as BDP.
PARTY_FIXES = {"BBDP": "BDP"}
DISPUTED: dict[str, str] = {}
# Lorolwane's total cast prints as 11 104, a typo: its 1 044 valid and 60 rejected votes add to 1 104.
CORRECTIONS = {"400": 1104}


ACCEPTED = {
    "accounting.votes_valid": "In wards 21, 108, 149, 237, 238, 433, and 484 the annexure's printed valid votes differ from "
    "the sum of its own candidate lines, and both are stored as printed; each ward's note says so",
}


class Ward(NamedTuple):
    code: str
    name: str
    council: str
    registered: int
    valid: int
    rejected: int
    cast: int
    margin: int
    candidates: list[tuple[str, str, int]]


def council_of(code: int) -> str:
    return next(name for low, high, name in COUNCILS if low <= code <= high)


def _artifact() -> raw.Artifact:
    try:
        return raw.cached(SOURCE, REPORT)
    except LookupError:
        for found in sorted(raw.keys(SOURCE)):
            if found.endswith("Botswana_2019_General_Elections_REPORT(1).pdf"):
                return raw.cached(SOURCE, found)
        raise


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


@functools.cache
def lines() -> list[str]:
    cache = raw.parsed(SOURCE) / "2019.txt"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        path = raw.DATA / _artifact().storage_key
        subprocess.run(["pdftotext", "-layout", str(path), str(cache)], check=True, capture_output=True)
    rows = [re.sub(r"\s+", " ", line).strip() for line in cache.read_text().split("\n")]
    # Annexure 2 starts after the contents page and ends at annexure 3, which both print their own heading.
    start = next(i for i, row in enumerate(rows) if row == FIRST_PAGE)
    end = next(i for i, row in enumerate(rows) if i > start and row == LAST_PAGE)
    return rows[start:end]


def _header(row: str) -> tuple[str, str, list[int], int] | None:
    match = HEADER.match(STARRED.sub("", row))
    if not match:
        return None
    return match[1], match[2], [int(n) for n in match[3].split()], int(match[5])


@functools.cache
def wards() -> list[Ward]:
    heads: list[tuple[str, str, list[int], int, list[tuple[str, str, int]]]] = []
    pending = ""
    after = False
    for row in lines():
        head = _header(row)
        if head:
            heads.append((*head, []))
            pending, after = "", True
            continue
        match = CANDIDATE.match(row)
        if match and heads:
            heads[-1][4].append(
                (match[1].strip().title(), PARTY_FIXES.get(match[3].upper(), match[3].upper()), int(match[4]))
            )
            pending, after = "", True
            continue
        # A page break can split a candidate: the names end one page and the gender, party, and votes start the next.
        split = SPLIT.match(row)
        if split and pending and heads:
            heads[-1][4].append((pending.title(), split[2].upper(), int(split[3])))
            pending, after = "", True
        elif after and NAMES.match(row) and row not in NOISE:
            pending, after = row, False
    # Two wards print twice, in full and identically, where a page repeats.
    found: dict[str, Ward] = {}
    for head in heads:
        ward = _settle(*head)
        if found.setdefault(ward.code, ward) != ward:
            raise ValueError(f"ward {ward.code} prints twice with different figures")
    return list(found.values())


def _settle(code: str, name: str, tail: list[int], margin: int, candidates: list[tuple[str, str, int]]) -> Ward:
    """Split the numbers from a name that ends in a number, and check the ward adds up the way the annexure prints it.

    The numbers after the name are the registered voters, the valid votes, the rejected votes when printed, and the
    total cast. The candidates' votes settle which of the numbers belong to the name.
    """
    votes = sum(v for _, _, v in candidates)
    for size in (4, 3):
        if len(tail) < size:
            continue
        registered, valid, *rest, cast = tail[-size:]
        rejected = rest[0] if rest else 0
        if valid == votes and valid + rejected == cast:
            full = " ".join([name, *(str(n) for n in tail[:-size])])
            ranked = sorted((v for _, _, v in candidates), reverse=True)
            if len(ranked) > 1 and ranked[0] - ranked[1] != margin:
                MARGINS.append(f"{code} {full}: printed {margin}, candidates {ranked[0] - ranked[1]}")
            return Ward(code, full, council_of(int(code)), registered, valid, rejected, cast, margin, candidates)
    # The annexure's own figures disagree here, so the ward is stored as printed and disclosed in its note.
    registered, valid, *rest, cast = tail[-4:] if len(tail) >= 4 else [tail[0], tail[1], 0, tail[2]]
    rejected = rest[0] if rest else 0
    name = " ".join([name, *(str(n) for n in tail[:-4])]) if len(tail) > 4 else name
    if code in CORRECTIONS:
        DISPUTED[code] = (
            f"The annexure prints the total cast as {cast:,}, a typo, and it is stored as {CORRECTIONS[code]:,}."
        )
        cast = CORRECTIONS[code]
    else:
        DISPUTED[code] = (
            f"The candidates' votes add to {votes:,}, but the annexure prints {valid:,} valid votes, and both are stored as printed."
        )
    return Ward(code, name, council_of(int(code)), registered, valid, rejected, cast, margin, candidates)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def ward_slug(ward: Ward) -> str:
    return f"{changeset.slug(ward.council)}:{changeset.slug(ward.name)}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{MAIN}", f"{SOURCE}/{COUNTERMANDED}"]


def parse() -> None:
    wards()
    check()
    geo().write()
    registry().write()
    election(MAIN).write()
    election(COUNTERMANDED).write()


def emit() -> None:
    package.run("load", packages())


def check() -> None:
    """Hold the wards to the report's counts: 490 wards, in numbered order, and its tally of seats won."""
    found = wards()
    if [int(w.code) for w in found] != list(range(1, 491)):
        raise ValueError("the annexure does not print wards 1 to 490 in order")
    seats: dict[str, int] = {}
    for ward in found:
        if ward.code in TIED:
            continue
        leader = max(ward.candidates, key=lambda c: c[2])
        seats[leader[1]] = seats.get(leader[1], 0) + 1
    if seats != SEATS:
        raise ValueError(f"the wards' seats do not match the report's: {seats} against {SEATS}")


def ward_area(ward: Ward) -> str:
    return key("area", "bw.ward", ward_slug(ward))


def council_area(name: str) -> str:
    slug = changeset.slug(name)
    return key("area", "bw.council", slug) if name == COUNCILS[0][2] else iec.area("bw.council", slug)


def council_body(name: str) -> str:
    return key("body", "council", changeset.slug(name)) if name == COUNCILS[0][2] else iec.council_body(name)


def party_id(code: str) -> str:
    return key("party", code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Botswana's council wards, and the Chobe District Council, as the Independent Electoral Commission's "
        "2019 general election report lists them",
        depends=[f"{iec.SOURCE}/geo"],
    )
    p.external |= {BOTSWANA, COUNTRY}
    p.artifact(_artifact(), iec.IEC_SOURCE)
    p.add(
        "scheme", code="bw.ward", polity_id=BOTSWANA, kind="electoral_district", rank=3, noun="ward", code_system=None
    )
    chobe = COUNCILS[0][2]
    id = council_area(chobe)
    p.add("area", id=id, scheme_code="bw.council")
    p.add("area_code", area_id=id, code=changeset.slug(chobe))
    p.add("area_parent", area_id=id, parent_id=COUNTRY)
    p.name(id, chobe.title(), "en", "official")
    for ward in wards():
        id = ward_area(ward)
        p.add("area", id=id, scheme_code="bw.ward")
        p.add("area_code", area_id=id, code=ward_slug(ward))
        p.add("area_parent", area_id=id, parent_id=council_area(ward.council))
        p.name(id, ward.name.title(), "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Botswana's Chobe District Council and the parties of the 2019 council elections",
        depends=[f"{SOURCE}/geo", f"{iec.SOURCE}/registry"],
    )
    p.external |= {BOTSWANA}
    chobe = council_body(COUNCILS[0][2])
    p.add("body", id=chobe, polity_id=BOTSWANA, kind="council")
    p.name(chobe, COUNCILS[0][2].title(), "en", "official")
    for code in sorted(PARTIES):
        id = party_id(code)
        p.add("party", id=id, kind="bloc" if code in COALITIONS else "party", polity_id=BOTSWANA)
        p.add("external_id", entity_id=id, scheme="bw.iec_council.party", value=code)
        p.name(id, PARTIES[code], "en", "official")
        p.name(id, code, "en", "abbreviation", False)
    return p


METHOD = (
    "Counted by the Independent Electoral Commission for each polling district, which elects one council ward, as "
    "the annexure to its 2019 general election report prints them. Registered voters are the roll, rejected votes "
    "are the ballots set aside, and ballots cast are the valid and rejected ballots together. The valid votes are "
    "the candidates' votes added up. The report declares each ward's winner through its winning margin, so the "
    "leading candidate is stored as elected; a ward whose leading candidates tied has no winner stored."
)


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


def election(day: date) -> Package:
    main = day == MAIN
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        (
            "The 23 October 2019 general election of Botswana: the local councils by ward"
            if main
            else "The 23 November 2019 countermanded council ward election at Boseja-Flowertown, Botswana"
        ),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{iec.SOURCE}/geo", f"{iec.SOURCE}/registry"],
    )
    p.external |= {BOTSWANA, COUNTRY}
    p.uses(_artifact())
    for code, reason in ACCEPTED.items():
        if main:
            p.accept(code, reason)
    election = p.id("election", day)
    path = f"bw/{day}"
    p.add(
        "election",
        id=election,
        polity_id=BOTSWANA,
        kind="general" if main else "by_election",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=f"{path}-councils", entity_id=election, is_current=True)
    p.name(
        election,
        "2019 Botswana general election, local councils" if main else "2019 Boseja-Flowertown council ward election",
        "en",
        "official",
    )
    for ward in wards():
        if (ward.code in COUNTERMANDED_WARDS) == main:
            continue
        slug = ward_slug(ward)
        unit = p.id("unit", day, ward.code, slug)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BOTSWANA,
            scheme_code="bw.ward",
            code=f"{day}:{ward.code}:{slug}",
            area_relation="equals",
            area_id=ward_area(ward),
            kind="standard",
        )
        contest = p.id("contest", day, ward.code, slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=council_body(ward.council),
            district_area_id=ward_area(ward),
            magnitude=1,
            round_number=1,
            electoral_system_id=iec.SYSTEM_FPTP,
            default_result_kind="counted",
        )
        p.name(contest, f"2019 local council election, {ward.name.title()} ({ward.council.title()})", "en", "official")
        p.add(
            "slug", path=f"{path}-councils/{ward.code}-{changeset.slug(ward.name)}", entity_id=contest, is_current=True
        )
        result_set = p.id("result_set", day, ward.code, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=iec.IEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        body = METHOD
        if ward.code in DISPUTED:
            body += f" {DISPUTED[ward.code]}"
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
        top = max(v for _, _, v in ward.candidates)
        leaders = [c for c in ward.candidates if c[2] == top]
        for index, (candidate, code, votes) in enumerate(ward.candidates, start=1):
            option = p.id("option", day, ward.code, slug, index)
            person = p.id("person", day, ward.code, slug, index)
            solo = code == "IND"
            p.add("person", id=person)
            p.name(person, candidate, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if solo else party_id(code),
                ballot_position=index,
                ballot_label=candidate,
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", day, ward.code, slug, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                member_of_party_id=None if solo else party_id(code),
                is_independent=solo,
            )
            if len(leaders) == 1 and votes == top:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            _value(p, result_set, unit, option, 1, votes)
        for measure, value in ((2, ward.registered), (4, ward.valid), (5, ward.rejected), (3, ward.cast)):
            _value(p, result_set, unit, None, measure, value)
    return p
