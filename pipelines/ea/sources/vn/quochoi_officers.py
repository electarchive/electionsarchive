"""Viet Nam's National Assembly votes electing the State's officers, from the Assembly's press releases and news.

The Assembly elects the President, the Prime Minister, the Chairman of the Assembly, the Vice President, the Chief
Justice of the Supreme People's Court, and the Procurator General of the Supreme People's Procuracy. Each is
chosen by secret ballot, and the Assembly then adopts a resolution electing the winner by electronic vote. The
Assembly's press releases print that vote: the deputies who took part and the deputies in favor. The ballot counts
from the vote counting committee's minutes are not published in these releases, so the resolution vote is the
figure stored.
"""

import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "vn.quochoi_officers"
VIETNAM = str(uuid5(package.NAMESPACE, "polity\x1fVN"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fVNM"))
SCHEME = "vn.nation"
HOME = "https://daibieunhandan.vn/"
RELEASE = "https://daibieunhandan.vn/thong-cao-bao-chi-so-{n}-ky-hop-thu-nhat-quoc-hoi-khoa-xvi-{id}.html"
VOTES, BALLOTS = 1, 3
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


PORTAL = key("source", "daibieunhandan")
GOVERNMENT = key("source", "vanban")
OFFICES = {
    "president": ("head_of_state", "President of Viet Nam", "Chủ tịch nước Cộng hòa xã hội chủ nghĩa Việt Nam"),
    "vice-president": ("other", "Vice President of Viet Nam", "Phó Chủ tịch nước Cộng hòa xã hội chủ nghĩa Việt Nam"),
    "prime-minister": ("head_of_government", "Prime Minister of Viet Nam", "Thủ tướng Chính phủ"),
    "chair": ("speaker", "Chairman of the National Assembly", "Chủ tịch Quốc hội"),
    "chief-justice": ("other", "Chief Justice of the Supreme People's Court", "Chánh án Tòa án nhân dân tối cao"),
    "procurator-general": (
        "other",
        "Procurator General of the Supreme People's Procuracy",
        "Viện trưởng Viện kiểm sát nhân dân tối cao",
    ),
}
SYSTEM = key("electoral_system", "assembly_vote")


@dataclass(frozen=True)
class Vote:
    office: str
    candidate: str
    present: int  # deputies who took part in the electronic vote on the resolution
    favor: int
    page: str  # the page that prints the vote
    needle: str  # text the page must contain, whitespace collapsed


@dataclass(frozen=True)
class Day:
    day: date
    title: str
    title_vi: str
    votes: tuple[Vote, ...]
    others: tuple[str, ...] = ()


def release(n: str, id: int) -> str:
    return RELEASE.format(n=n, id=id)


NEWS_2024 = "https://daibieunhandan.vn/{slug}.html"
DAYS = (
    Day(
        date(2026, 4, 6),
        "Election of the Chairman of the 16th National Assembly",
        "Bầu Chủ tịch Quốc hội khóa XVI",
        (
            Vote(
                "chair",
                "Trần Thanh Mẫn",
                491,
                491,
                release("01", 10412423),
                "có 491 đại biểu tham gia biểu quyết, trong đó có 491 đại biểu tán thành (bằng 100% đại biểu Quốc hội có mặt và bằng 98.20% tổng số ĐBQH). Vào 11 giờ 05",
            ),
        ),
    ),
    Day(
        date(2026, 4, 7),
        "Elections of the President, Vice President, Prime Minister, Chief Justice, and Procurator General",
        "Bầu Chủ tịch nước, Phó Chủ tịch nước, Thủ tướng Chính phủ, Chánh án Tòa án nhân dân tối cao và Viện trưởng Viện kiểm sát nhân dân tối cao",
        (
            Vote(
                "president",
                "Tô Lâm",
                495,
                495,
                release("02", 10412498),
                "có 495 đại biểu tham gia biểu quyết, trong đó, có 495 đại biểu tán thành (bằng 100% đại biểu Quốc hội có mặt và bằng 99.00% tổng số ĐBQH). Theo đó, đồng chí Tô Lâm",
            ),
            Vote(
                "prime-minister",
                "Lê Minh Hưng",
                495,
                495,
                release("03", 10412572),
                "có 495 đại biểu tham gia biểu quyết, trong đó, có 495 đại biểu tán thành (bằng 100% ĐBQH có mặt và bằng 99% tổng số ĐBQH). Theo đó, đồng chí Lê Minh Hưng",
            ),
            Vote(
                "chief-justice",
                "Nguyễn Văn Quảng",
                496,
                496,
                release("03", 10412572),
                "có 496 đại biểu tham gia biểu quyết, trong đó, có 496 đại biểu tán thành (bằng 100% ĐBQH có mặt và bằng 99,20% tổng số ĐBQH). Theo đó, đồng chí Nguyễn Văn Quảng",
            ),
            Vote(
                "vice-president",
                "Võ Thị Ánh Xuân",
                485,
                485,
                release("03", 10412572),
                "có 485 đại biểu tham gia biểu quyết, trong đó, có 485 đại biểu tán thành (bằng 100% ĐBQH có mặt và bằng 97% tổng số ĐBQH). Theo đó, đồng chí Võ Thị Ánh Xuân",
            ),
            Vote(
                "procurator-general",
                "Nguyễn Huy Tiến",
                474,
                474,
                release("03", 10412572),
                "có 474 đại biểu tham gia biểu quyết, trong đó, có 474 đại biểu tán thành (bằng 100% ĐBQH có mặt và bằng 94,80% tổng số ĐBQH). Theo đó, đồng chí Nguyễn Huy Tiến",
            ),
        ),
        ("https://vanban.chinhphu.vn/?docid=217710&pageid=27160",),
    ),
    Day(
        date(2024, 5, 22),
        "Election of the President of Viet Nam, 2021–2026 term",
        "Bầu Chủ tịch nước nhiệm kỳ 2021 - 2026",
        (
            Vote(
                "president",
                "Tô Lâm",
                473,
                472,
                NEWS_2024.format(slug="chu-tich-nuoc-to-lam-tuyen-the-nham-chuc-post372494"),
                "với 472/473 đại biểu tham gia biểu quyết tán thành, chiếm 96,92% tổng số đại biểu Quốc hội có mặt tán thành",
            ),
        ),
    ),
    Day(
        date(2024, 8, 26),
        "Elections of the Chief Justice and the Procurator General, 2021–2026 term",
        "Bầu Chánh án Tòa án nhân dân tối cao và Viện trưởng Viện kiểm sát nhân dân tối cao nhiệm kỳ 2021-2026",
        (
            Vote(
                "chief-justice",
                "Lê Minh Trí",
                438,
                438,
                NEWS_2024.format(
                    slug="quoc-hoi-bau-chanh-an-toa-an-nhan-dan-toi-cao-doi-voi-ong-le-minh-tri-post385129"
                ),
                "với kết quả 438/438 đại biểu tán thành, bằng 91,06% tổng số ĐBQH",
            ),
            Vote(
                "procurator-general",
                "Nguyễn Huy Tiến",
                439,
                439,
                NEWS_2024.format(
                    slug="quoc-hoi-bau-chanh-an-toa-an-nhan-dan-toi-cao-doi-voi-ong-le-minh-tri-post385129"
                ),
                "ông Nguyễn Huy Tiến với tỷ lệ 439/439 đại biểu tham gia biểu quyết tán thành, bằng 91,27% tổng số ĐBQH",
            ),
        ),
    ),
)

METHOD = (
    "The Assembly elects each officer by secret ballot, nominated in a proposal from the President or from its Standing "
    "Committee, and then adopts a resolution electing the winner by electronic vote. The press release prints that "
    "vote: the deputies who took part and the deputies in favor. The resolution has no option to vote against, "
    "so these two figures are the whole record, and where they differ the difference is not broken down. The "
    "ballot counts of the vote counting committee are not published there and are not stored here."
)


def election_key(d: Day) -> str:
    return f"{SOURCE}/{d.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(election_key(d) for d in DAYS)]


def urls(d: Day) -> list[str]:
    seen: dict[str, None] = {}
    for v in d.votes:
        seen[v.page] = None
    for url in d.others:
        seen[url] = None
    return list(seen)


def fetch() -> None:
    for d in DAYS:
        for url in urls(d):
            raw.fetch(SOURCE, url, suffix=".html")


def text(url: str) -> str:
    body = raw.cached(SOURCE, url).path.read_text("utf-8", "replace")
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", body).split())


def parse() -> None:
    from html import unescape

    for d in DAYS:
        for v in d.votes:
            if unescape(v.needle) not in unescape(text(v.page)):
                raise ValueError(f"{d.day} {v.office}: the page does not contain {v.needle!r}")
            if v.favor > v.present:
                raise ValueError(f"{d.day} {v.office}: more in favor than present")
    registry().write()
    for d in DAYS:
        election(d).write()


def emit() -> None:
    package.run("load", packages())


def _sources(p: Package) -> None:
    p.add(
        "source",
        id=PORTAL,
        kind="official_body",
        publisher="Báo Đại biểu Nhân dân",
        title="Press releases and reports of the sessions of the National Assembly of Viet Nam",
        url=HOME,
        license="No license stated; published for public information",
        attribution="Báo Đại biểu Nhân dân, the National Assembly's newspaper",
    )
    p.add(
        "source",
        id=GOVERNMENT,
        kind="official_body",
        publisher="Cục Thông tin và Truyền thông Chính phủ",
        title="Resolutions of the National Assembly, Government document system",
        url="https://vanban.chinhphu.vn/",
        license="No license stated; published for public information",
        attribution="Cục Thông tin và Truyền thông Chính phủ",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Viet Nam's State offices that the National Assembly elects, and the electoral system of those votes",
    )
    p.external.add(VIETNAM)
    p.add("scheme", code=SCHEME, polity_id=VIETNAM, kind="administrative", rank=1, noun="nation", code_system=None)
    for slug, (kind, en, vi) in OFFICES.items():
        office = key("office", slug)
        p.add("office", id=office, polity_id=VIETNAM, kind=kind)
        p.name(office, en, "en", "official")
        p.name(office, vi, "vi", "official", False)
    p.add("electoral_system", id=SYSTEM, polity_id=VIETNAM, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=METHOD,
        lang="en",
    )
    return p


def election(d: Day) -> Package:
    day = d.day
    p = Package(
        election_key(d),
        SOURCE,
        f"{d.title}, {day:%-d %B %Y}, from the Assembly's press releases",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {VIETNAM, COUNTRY}
    _sources(p)
    for url in urls(d):
        p.artifact(raw.cached(SOURCE, url), GOVERNMENT if "vanban." in url else PORTAL)
    eid = key("election", day)
    path = f"vn/{day}-assembly-vote"
    p.add("election", id=eid, polity_id=VIETNAM, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=eid, is_current=True)
    p.name(eid, f"{day:%-d %B %Y} National Assembly vote: {d.title}", "en", "official")
    p.name(eid, d.title_vi, "vi", "native", False)
    unit = key("unit", day)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=VIETNAM,
        scheme_code=SCHEME,
        code="VN",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    rows: list[tuple[str, str | None, int, int]] = []
    for v in d.votes:
        office = key("office", v.office)
        contest = key("contest", day, v.office)
        _, en, vi = OFFICES[v.office]
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="office",
            office_id=office,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{day.year} election of the {en}", "en", "official")
        p.name(contest, f"Bầu {vi}", "vi", "native", False)
        p.add("slug", path=f"{path}/{v.office}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, v.office)
        p.add(
            "result_set",
            id=result_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=PORTAL,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        person = key("person", day, v.office)
        p.add("person", id=person)
        p.name(person, v.candidate, "vi", "official")
        option = key("option", day, v.office)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=1,
            ballot_label=v.candidate,
        )
        p.add(
            "candidacy",
            id=key("candidacy", day, v.office),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=v.candidate,
            is_independent=False,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=1)
        rows += [
            (result_set, option, VOTES, v.favor),
            (result_set, None, BALLOTS, v.present),
        ]
    p.frame(
        "result_value",
        pl.DataFrame(
            [(rs, unit, opt, measure, n) for rs, opt, measure, n in rows],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    for rs, opt, measure, n in rows:
        p.add("declared", result_set_id=rs, option_id=opt, measure=measure, value=n)
    return p
