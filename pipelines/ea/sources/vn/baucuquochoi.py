"""Viet Nam's National Assembly election of 15 March 2026 from the election news site baucuquochoi.vn.

The site lists the 500 deputies elected to the sixteenth Assembly with the province or city each stood in and the
share of the vote each won. It does not print the constituency (there were 182) or any vote count, so the 34
provinces and cities are the finest unit, and the only figures are the winners' shares. The national electorate and
turnout come from the press briefing on the National Election Council's Resolution 232.
"""

import re
import unicodedata
from datetime import date
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "vn.baucuquochoi"
SITE = "https://baucuquochoi.vn"
LIST = f"{SITE}/ky-hop/xvi-189.html"
NEWS = "https://daibieunhandan.vn/ty-le-cu-tri-tham-gia-bo-phieu-cao-nhat-tu-truoc-den-nay-10410736.html"
VIETNAM = str(uuid5(package.NAMESPACE, "polity\x1fVN"))
DAY = date(2026, 3, 15)
REGISTERED, CAST, SHARE = 2, 3, 13
SHARE_ONLY = 4
ELECTORATE, VOTERS = 76_423_940, 76_198_214
SEATS = 500


GENDERS = {"Nam": "male", "Nữ": "female"}


class Deputy(NamedTuple):
    number: str
    name: str
    gender: str
    born: int
    province: str
    share: int


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


PORTAL = key("source", "portal")
PRESS = key("source", "press")
ASSEMBLY = key("body", "national_assembly")
SYSTEM = key("electoral_system", "block")
METHOD = (
    "The site prints, for each of the 500 deputies elected, the province or city where they stood and the share of "
    "the vote they won, in percent to as many as four places. It prints no vote counts and no constituency, so each province's "
    "contest has one reporting unit, the province, and the shares are stored as printed, in ten-thousandths of a "
    "percent, with no counts derived from them. Each province's magnitude is the number of its deputies listed. "
    "The losing candidates (863 stood for 500 seats) are not listed."
)
TOTALS = (
    "The national figures are those the Council's office reported at the press briefing on its Resolution 232: "
    "76,423,940 voters on the rolls and about 76,198,214 who voted, 99.70 percent, from the preliminary reports of "
    "the 34 provinces and cities."
)


def fetch() -> None:
    raw.fetch(SOURCE, LIST, suffix=".html")
    raw.fetch(SOURCE, NEWS, suffix=".html")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def read() -> list[Deputy]:
    """Each deputy once, with the share in ten-thousandths of a percent."""
    body = raw.cached(SOURCE, LIST).path.read_text(encoding="utf-8")
    found: dict[str, Deputy] = {}
    for href, name, sex, block in re.findall(
        r'<a class="item-member" href="([^"]+)" title="([^"]*)"[^>]*?data-filter-sex="([^"]*)"[^>]*>(.*?)</a>',
        body,
        re.DOTALL,
    ):
        fields = dict(re.findall(r"<span>([^<:]*):</span>\s*([^<]*)", block))
        share = re.fullmatch(r"(\d{1,3}),(\d{1,4})%", fields["Tỷ lệ trúng cử"].strip())
        number = re.search(r"XVI-(\d+)\.html", href)
        if share is None or number is None or sex not in GENDERS:
            raise ValueError(f"unreadable deputy entry: {href}")
        row = Deputy(
            number[1],
            clean(name),
            GENDERS[sex],
            int(fields["Năm sinh"]),
            clean(fields["Đơn vị ứng cử"]),
            int(share[1]) * 10_000 + int(share[2].ljust(4, "0")),
        )
        if found.setdefault(number[1], row) != row:
            raise ValueError(f"deputy {href} is listed twice with different details")
    rows = list(found.values())
    if len(rows) != SEATS:
        raise ValueError(f"{len(rows)} deputies listed, expected {SEATS}")
    return rows


def check_totals() -> None:
    text = raw.cached(SOURCE, NEWS).path.read_text(encoding="utf-8")
    for phrase in ("76.198.214/ 76.423.940", "99,70%", "Tổng số ĐBQH được bầu là 500", "232/NQ-HĐBCQH"):
        if phrase not in text:
            raise ValueError(f"the press page no longer prints {phrase!r}")
    if round(VOTERS / ELECTORATE * 100, 2) != 99.70:
        raise ValueError("printed turnout does not match the printed counts")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Viet Nam's National Assembly, its provinces as electoral groupings, and its electoral system",
    )
    p.external.add(VIETNAM)
    p.add(
        "source",
        id=PORTAL,
        kind="news_agency",
        publisher="Trang tin Bầu cử Quốc hội khóa XVI (baucuquochoi.vn)",
        title="List of the 500 deputies of the sixteenth National Assembly",
        url=LIST,
        license="Not stated",
        attribution="baucuquochoi.vn",
    )
    p.add(
        "source",
        id=PRESS,
        kind="news_agency",
        publisher="Đại biểu Nhân dân (daibieunhandan.vn)",
        title="Report of the press briefing on the National Election Council's Resolution 232/NQ-HĐBCQG",
        url=NEWS,
        license="Not stated",
        attribution="daibieunhandan.vn",
    )
    p.artifact(raw.cached(SOURCE, LIST), PORTAL)
    p.artifact(raw.cached(SOURCE, NEWS), PRESS)
    p.add("scheme", code="vn.province", polity_id=VIETNAM, kind="administrative", rank=1, noun="province or city")
    p.add("body", id=ASSEMBLY, polity_id=VIETNAM, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    p.name(ASSEMBLY, "Quốc hội", "vi", "native", False)
    p.add("electoral_system", id=SYSTEM, polity_id=VIETNAM, rules={"formula": "block"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Each constituency elects two to five deputies. A voter may mark as many candidates as the constituency "
            "has seats, and the candidates with the most votes win, provided each has more than half of the valid "
            "votes."
        ),
        lang="en",
    )
    return p


def build() -> Package:
    check_totals()
    rows = read()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Viet Nam's National Assembly election of 15 March 2026: the 500 winners by province or city, with their "
        "shares of the vote, and the national electorate and turnout",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(VIETNAM)
    p.uses(raw.cached(SOURCE, LIST))
    p.uses(raw.cached(SOURCE, NEWS))
    election = key("election", DAY)
    p.add("election", id=election, polity_id=VIETNAM, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=f"vn/{DAY}-general", entity_id=election, is_current=True)
    p.name(election, "2026 Vietnamese National Assembly election", "en", "official")
    p.name(election, "Bầu cử đại biểu Quốc hội khóa XVI", "vi", "native", False)

    def result_set(tag: str, contest: str | None, note: str) -> str:
        found = key("result_set", tag)
        p.add(
            "result_set",
            id=found,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=PORTAL if contest else PRESS,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", found), entity_id=found, kind="methodology", body_md=note, lang="en")
        return found

    def unit(code: str, name: str, kind: str) -> str:
        found = key("unit", code)
        p.add(
            "reporting_unit",
            id=found,
            polity_id=VIETNAM,
            scheme_code="vn.province",
            code=f"{DAY}:{code}",
            area_relation="unlocated",
            area_id=None,
            kind=kind,
        )
        p.name(found, name, "vi", "official")
        return found

    def put(rs: str, at: str, option: str | None, measure: int, value: int, flags: int = 0) -> None:
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=at,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=value,
        )

    national = result_set("national", None, TOTALS)
    nation = unit("national", "Việt Nam", "aggregate")
    put(national, nation, None, REGISTERED, ELECTORATE)
    put(national, nation, None, CAST, VOTERS)
    provinces: dict[str, list[Deputy]] = {}
    for row in rows:
        provinces.setdefault(row.province, []).append(row)
    for name in sorted(provinces):
        deputies = sorted(provinces[name], key=lambda row: (-row.share, row.name, row.number))
        tag = slug(name)
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=ASSEMBLY,
            magnitude=len(deputies),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"2026 National Assembly election, {name}", "en", "official")
        p.name(contest, f"Bầu cử đại biểu Quốc hội khóa XVI, {name}", "vi", "native", False)
        p.add("slug", path=f"vn/{DAY}-general/national-assembly-{tag}", entity_id=contest, is_current=True)
        rs = result_set(tag, contest, METHOD)
        at = unit(tag, name, "standard")
        for position, (number, person_name, gender, born, _, share) in enumerate(deputies, 1):
            person = key("person", number)
            p.add("person", id=person, birth_date=date(born, 1, 1), birth_precision="year", gender=gender)
            p.name(person, person_name, "vi", "official")
            option = key("option", number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=person_name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=person_name,
            )
            put(rs, at, option, SHARE, share, SHARE_ONLY)
            p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    if sum(len(v) for v in provinces.values()) != SEATS or len(provinces) != 34:
        raise ValueError(f"{len(provinces)} provinces for {SEATS} seats")
    return p


def parse() -> None:
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())
