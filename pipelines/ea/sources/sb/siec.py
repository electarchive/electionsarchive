"""The Solomon Islands Electoral Commission's gazetted results of the 17 April 2024 joint election.

The Commission publishes each election's results as an extra-ordinary gazette: one signed notice from each returning
officer, listing every candidate's votes, the rejected votes, the electors who voted, and the registered electors. The
page of declared winners names each winner's party. The notices are the finest unit the Commission publishes: one per
constituency or ward.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from html import unescape
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "sb.siec"
COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fSB"))
DAY = date(2024, 4, 17)
PAGE = "https://siec.gov.sb/elections/list-of-constituencies-and-winners/"
UPLOADS = "https://siec.gov.sb/wp-content/uploads/2024/05"
GAZETTES = {
    "general": f"{UPLOADS}/Gaz-No.-70-Tuesday-30th-April-2024.pdf",
    "provincial": f"{UPLOADS}/Gaz-No.-71-Tuesday-30th-April-2024.pdf",
    "honiara": f"{UPLOADS}/Gaz-No.-72-Tuesday-30th-April-2024.pdf",
    "uncontested": f"{UPLOADS}/Gaz-No.-73-Tuesday-30th-April-2024.pdf",
}
VOTES, REGISTERED, CAST, INVALID = 1, 2, 3, 5

METHOD = (
    "Published in the Commission's extra-ordinary gazette of 30 April 2024 as the returning officer's notice for the "
    "{place}: each candidate's votes, the rejected votes, the registered electors recorded as voted, and the "
    "registered electors. The Commission publishes no valid-vote total and no national total. Figures are stored as "
    "printed."
)
UNCONTESTED_METHOD = (
    "Published in the Commission's extra-ordinary gazette of 30 April 2024 as the returning officer's declaration that "
    "the {place} seat was uncontested: one valid nomination was received, so its sole candidate is declared elected. "
    "No votes were cast, and the notice gives no electors figures."
)
# The Kaloka and Matakwalao notices print the form's placeholder, "(Name of province)", where the province belongs;
# the Commission's page of declared winners lists the seats under Isabel and Malaita.
PLACEHOLDER = {"Kaloka": "Isabel", "Matakwalao": "Malaita"}
KINDS = {
    "general": ("Solomon Islands national general election", "constituency", "constituencies"),
    "provincial": ("Solomon Islands provincial assembly elections", "ward", "wards"),
    "honiara": ("Honiara City Council election", "ward", "wards"),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SIEC_SOURCE = key("source", "siec")
PARLIAMENT = key("body", "parliament")
HONIARA_COUNCIL = key("body", "honiara-council")
SYSTEM = key("electoral_system", "fptp")


@dataclass
class Notice:
    election: str
    name: str
    province: str
    winner: str
    candidates: list[tuple[int, str, int]]
    rejected: int
    voted: int
    registered: int
    uncontested: bool = False


def clean(text: str) -> str:
    return " ".join(text.split())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold().replace("’", "").replace("'", "")).strip("-")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def text_of(election: str) -> str:
    path = raw.cached(SOURCE, GAZETTES[election]).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return re.sub(r"^[ \t\f]*\d+[ \t]*\n", "", done.stdout, flags=re.MULTILINE)  # page numbers


def uncontested(election: str) -> list[Notice]:
    """The seats Gazette 73 declares uncontested, each with its sole candidate as printed (surname first)."""
    found: list[Notice] = []
    for part in re.split(r"(?=NOTIFICATION OF SUCCESSFUL CANDIDATE)", text_of("uncontested"))[1:]:
        officer = re.search(
            r"Returning Officer for the\s+(.+?)\s+(Provincial|Council)\s+Ward\s+in,?\s+(.+?)\s+hereby", part, re.DOTALL
        )
        sole = re.search(r"namely\s+(.+?),\s+has\s+met", part, re.DOTALL)
        if officer is None or sole is None:
            raise ValueError(f"unreadable uncontested notice: {part[:200]!r}")
        if (officer.group(2) == "Council") != (election == "honiara"):
            continue
        province = "" if election == "honiara" else re.sub(r"\s+Province$", "", clean(officer.group(3)))
        found.append(
            Notice(
                election,
                clean(officer.group(1)),
                province,
                clean(sole.group(1)),
                [(1, clean(sole.group(1)), 0)],
                0,
                0,
                0,
                True,
            )
        )
    return found


def notices(election: str) -> list[Notice]:
    found: list[Notice] = []
    for part in re.split(r"(?=NOTIFICATION OF SUCCESSFUL CANDIDATE)", text_of(election))[1:]:
        officer = re.search(r"Returning Officer for\s+(.+?)\s+provide\s+you", part, re.DOTALL)
        winner = re.search(r"advise that\s+(.*?)\s*received\s+more\s+votes", part, re.DOTALL)
        seat = re.search(
            r"successful\s+candidate\s+for\s+(?:the\s+)?(.+?)\.\s+For\s+your\s+information", part, re.DOTALL
        )
        if officer is None or winner is None or seat is None:
            raise ValueError(f"unreadable notice: {part[:200]!r}")
        text = clean(officer.group(1)).rstrip(",")
        name = re.sub(
            r"\s*/\s*", "/", re.sub(r"\s+(?:Constituency|wards?)$", "", clean(seat.group(1)), flags=re.IGNORECASE)
        )
        province = ""
        if election == "provincial":
            text = text.replace("(Name of province)", PLACEHOLDER.get(name, "") + " Province")
            found_ward = re.fullmatch(r".+?\s+of\s+(.+?)(?:\s+Provinces?)?", text, re.IGNORECASE)
            if found_ward is None:
                raise ValueError(f"unreadable ward: {text!r}")
            province = found_ward.group(1)
        table = re.search(r"ORDER BY(.*?)Total number of rejected", part, re.DOTALL)
        if table is None:
            raise ValueError(f"no candidate table for {text}")
        candidates = [
            (position, clean(label + tail), number(votes))
            for position, (label, tail, votes) in enumerate(
                re.findall(
                    r"^[ \t]*(?:\d+[ \t]+)?([A-Za-z\u2019'].*?)[ \t]{2,}([A-Za-z]*)([\d,]+)[ \t]*$",
                    table.group(1),
                    re.MULTILINE,
                ),
                1,
            )
        ]  # a printed rank can be missing at a page break, and a letter of the name can slip into the votes column
        if not candidates:
            raise ValueError(f"no candidates for {text}")
        rejected = re.search(r"rejected votes\s+([\d,]+)", part)
        voted = re.search(r"recorded as voted\s+([\d,]+)", part)
        registered = re.search(r"registered electors[ \t]+([\d,]+)[ \t]*$", part, re.MULTILINE)
        if rejected is None or voted is None or registered is None:
            raise ValueError(f"missing totals for {text}")
        found.append(
            Notice(
                election,
                name,
                province,
                clean(winner.group(1)),
                candidates,
                number(rejected.group(1)),
                number(voted.group(1)),
                number(registered.group(1)),
            )
        )
    return found + (uncontested(election) if election != "general" else [])


def winners() -> list[tuple[str, str, str, str]]:
    """The declared winners page: province, seat, seat type, name, and party for each row."""
    html = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8")
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL):
        cells = [
            clean(unescape(re.sub(r"<[^>]+>", "", c))) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if len(cells) == 6 and cells[2] in ("Parliamentary", "Provincial Assembly", "HCC") and cells[3]:
            rows.append((cells[1], cells[2], cells[3], cells[5]))
    return rows


def tokens(text: str) -> tuple[str, ...]:
    return tuple(sorted(re.sub(r"[^a-z0-9 ]", " ", text.casefold().replace("’", "").replace("'", "")).split()))


def seat_key(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", text.casefold().replace("’", "").replace("'", "")).split()
    return " ".join(w for w in words if w not in ("and", "constituency", "ward"))


KIND_OF_SEAT = {"Parliamentary": "general", "Provincial Assembly": "provincial", "HCC": "honiara"}


def similar(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def affiliation(election: str, notice: Notice, table: list[tuple[str, str, str, str]]) -> str | None:
    """The party the page of winners gives the notice's first candidate, matching seat and name leniently.

    The page and the gazettes spell seats and names differently (word order, an apostrophe, a typo), so a row matches
    when its seat reads like the notice's and its name shares letters with the winning candidate's.
    """
    seat = seat_key(notice.name)
    name = " ".join(tokens(notice.candidates[0][1]))
    best = 0.0
    party = None
    for row_seat, kind, row_name, row_party in table:
        if KIND_OF_SEAT[kind] != election:
            continue
        a, b = similar(seat_key(row_seat), seat), similar(" ".join(tokens(row_name)), name)
        if (a >= 0.85 and b >= 0.4 or a >= 0.75 and b >= 0.9) and a + b > best:
            best, party = a + b, row_party
    return party


def parties() -> list[tuple[str, str, str, str]]:
    return winners()


def party_name(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", label)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def fetch() -> None:
    for url in GAZETTES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, PAGE, suffix=".html")


def parse() -> None:
    geo().write()
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


def province_names() -> list[str]:
    return sorted({n.province for n in notices("provincial")})


def area_id(election: str, name: str) -> str:
    return key("area", election, slug(name))


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "The Solomon Islands electoral constituencies and wards of 2024, by name")
    p.external.add(COUNTRY)
    schemes = (
        ("sb.province", 1, "administrative", "province"),
        ("sb.constituency", 2, "electoral_district", "constituency"),
        ("sb.ward", 3, "electoral_district", "ward"),
    )
    for code, rank, kind, noun in schemes:
        p.add("scheme", code=code, polity_id=COUNTRY, kind=kind, rank=rank, noun=noun)
    for name in province_names() + ["Honiara"]:
        area = key("area", "province", slug(name))
        p.add("area", id=area, scheme_code="sb.province")
        p.add("area_code", area_id=area, code=slug(name))
        p.name(area, name, "en", "official")
    for election in KINDS:
        for notice in notices(election):
            scheme = "sb.constituency" if election == "general" else "sb.ward"
            area = area_id(election, f"{notice.province} {notice.name}")
            p.add("area", id=area, scheme_code=scheme)
            code = (
                f"{election}:{slug(notice.province)}:{slug(notice.name)}"
                if notice.province
                else (f"{election}:{slug(notice.name)}")
            )
            p.add("area_code", area_id=area, code=code)
            p.name(area, notice.name, "en", "official")
            if election != "general":
                parent = key("area", "province", slug(notice.province or "Honiara"))
                p.add("area_parent", area_id=area, parent_id=parent)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Solomon Islands parties, the National Parliament, the provincial assemblies, and the Honiara City Council",
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=SIEC_SOURCE,
        kind="official_body",
        publisher="Solomon Islands Electoral Commission",
        title="Gazetted results of the 2024 national, provincial assembly, and Honiara City Council elections",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Solomon Islands Electoral Commission",
    )
    for url in GAZETTES.values():
        p.artifact(raw.cached(SOURCE, url), SIEC_SOURCE)
    p.artifact(raw.cached(SOURCE, PAGE), SIEC_SOURCE)
    p.add("body", id=PARLIAMENT, polity_id=COUNTRY, kind="unicameral")
    p.name(PARLIAMENT, "National Parliament of Solomon Islands", "en", "official")
    p.add("body", id=HONIARA_COUNCIL, polity_id=COUNTRY, kind="council")
    p.name(HONIARA_COUNCIL, "Honiara City Council", "en", "official")
    for name in province_names():
        body = key("body", "assembly", slug(name))
        p.add("body", id=body, polity_id=COUNTRY, kind="council")
        p.name(body, f"{name} Provincial Assembly", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=COUNTRY, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each constituency or ward elects one member, the candidate with the most votes.",
        lang="en",
    )
    table = parties()
    for label in sorted({row[3] for row in table}):
        if label.casefold() == "independent":
            continue
        party = key("party", slug(party_name(label)))
        p.add("party", id=party, kind="party", polity_id=COUNTRY)
        p.name(party, party_name(label), "en", "official")
        short = re.search(r"\(([^)]+)\)\s*$", label)
        if short:
            p.name(party, short.group(1), "en", "abbreviation", False)
    return p


def build() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The Solomon Islands joint election of 17 April 2024 by constituency and ward, from the Electoral Commission's "
        "gazettes, with the three uncontested wards",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(COUNTRY)
    for url in GAZETTES.values():
        p.uses(raw.cached(SOURCE, url))
    p.uses(raw.cached(SOURCE, PAGE))
    table = parties()
    totals: dict[str, list[int]] = {}
    for election, (title, noun, _plural) in KINDS.items():
        entity = key("election", election)
        p.add(
            "election",
            id=entity,
            polity_id=COUNTRY,
            kind="general" if election == "general" else "local",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        p.name(entity, f"2024 {title}", "en", "official")
        p.add("slug", path=f"sb/{DAY}-{election}", entity_id=entity, is_current=True)
        for notice in notices(election):
            _contest(p, election, entity, noun, notice, table)
            totals.setdefault(election, []).append(sum(c[2] for c in notice.candidates))
    return p


def _contest(
    p: Package, election: str, entity: str, noun: str, notice: Notice, table: list[tuple[str, str, str, str]]
) -> None:
    where = f"{notice.province} {notice.name}".strip()
    code = (
        f"{election}:{slug(notice.province)}:{slug(notice.name)}"
        if notice.province
        else f"{election}:{slug(notice.name)}"
    )
    place = f"{notice.name} {noun}" + (f", {notice.province} Province" if notice.province else "")
    area = area_id(election, where)
    if election == "general":
        body = PARLIAMENT
    elif election == "honiara":
        body = HONIARA_COUNCIL
    else:
        body = key("body", "assembly", slug(notice.province))
    contest = key("contest", code)
    p.add(
        "contest",
        id=contest,
        election_id=entity,
        kind="body_seats",
        body_id=body,
        district_area_id=area,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"2024 {place}", "en", "official")
    p.add("slug", path=f"sb/{DAY}-{election}/{slug(where)}", entity_id=contest, is_current=True)
    result_set = key("result_set", code)
    p.add(
        "result_set",
        id=result_set,
        election_id=entity,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SIEC_SOURCE,
        status="active",
        is_default=True,
        completeness=0.0 if notice.uncontested else 1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=(UNCONTESTED_METHOD if notice.uncontested else METHOD).format(place=place),
        lang="en",
    )
    unit = key("unit", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=COUNTRY,
        scheme_code="sb.constituency" if election == "general" else "sb.ward",
        code=code,
        area_relation="equals",
        area_id=area,
        kind="standard",
    )
    p.name(unit, place, "en", "official")
    if not notice.uncontested:
        for measure, value in ((REGISTERED, notice.registered), (CAST, notice.voted), (INVALID, notice.rejected)):
            _put(p, result_set, unit, None, measure, value)
    party = affiliation(election, notice, table)
    for rank, label, votes in notice.candidates:
        option = key("option", code, rank)
        person = key("person", code, rank)
        winner = rank == 1
        known = winner and party is not None
        independent = known and party is not None and party.casefold() == "independent"
        member = None if not known or independent or party is None else key("party", slug(party_name(party)))
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=member,
            ballot_position=rank,
            ballot_label=label,
        )
        p.add("person", id=person)
        p.name(person, label, "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", code, rank),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
            member_of_party_id=member,
            is_independent=True if independent else None,
        )
        if winner:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        if not notice.uncontested:
            _put(p, result_set, unit, option, VOTES, votes)


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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
