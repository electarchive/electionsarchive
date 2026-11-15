from datetime import date
from itertools import pairwise
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import connect

SOURCE = "kr.nec_councillors"
DAY = date(1960, 7, 29)
BOOK = "https://library.nec.go.kr/neweps/epdata/EP24/EBM2024/01/EBM0120240243"
PAGE_IMAGE = BOOK + "/assets/page-images/page-4040d905-5e205f93-{:04d}.jpg"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLE = "대한민국선거사 제1집 (History of Elections in the Republic of Korea, vol. 1), 초대 참의원의원 당선자 명부 및 각종 통계"
LICENSE = "No license stated: published history of the National Election Commission; facts published with attribution"
SYSTEM_RULES = {"formula": "limited"}
METHOD = (
    "Votes of the elected councillors, as the National Election Commission's 대한민국선거사 제1집 prints them in its "
    "list of the first House of Councillors' members (pp. 1185 to 1189), with the province's electors, voters, valid "
    "ballots, and invalid ballots from its table of the councillors' election (p. 1190). Votes of candidates who "
    "lost are not printed, so only the elected appear. Each voter could mark up to half the province's seats, so "
    "candidate votes add up to more than the valid ballots. The first councillors drew lots for terms: the top half "
    "of each province's winners by votes (rounded up where the seat count is odd) sat the full six years (제1부), "
    "and the rest three years (제2부)."
)
PARTIES = {
    "democratic": ("민주당", "Democratic Party"),
    "liberal": ("자유당", "Liberal Party"),
    "social_masses": ("사회대중당", "Socialist Masses Party"),
    "korean_socialist": ("한국사회당", "Korean Socialist Party"),
    "reform_comrades": ("혁신동지총연맹", "Reform Comrades' League"),
}
D, L, S, K, R = "democratic", "liberal", "social_masses", "korean_socialist", "reform_comrades"
Row = tuple[str, str, str | None, int, int]
PROVINCES: list[tuple[str, str, str, int, list[Row]]] = [
    (
        "11",
        "서울특별시",
        "Seoul",
        1186,
        [
            ("백낙준", "白樂濬", None, 328485, 1),
            ("한통숙", "韓通淑", D, 183020, 1),
            ("김동명", "金東鳴", D, 166780, 1),
            ("이인", "李仁", None, 154748, 2),
            ("고희동", "高羲東", D, 152066, 2),
            ("전용순", "全用淳", D, 146231, 2),
        ],
    ),
    (
        "41",
        "경기도",
        "Gyeonggi",
        1186,
        [
            ("여운홍", "呂運弘", None, 439755, 1),
            ("정낙필", "鄭樂弼", D, 196310, 1),
            ("신의식", "申義湜", L, 179313, 1),
            ("김용성", "金龍星", D, 170282, 2),
            ("이교선", "李敎善", None, 162734, 2),
            ("하상훈", "河相勳", D, 146783, 2),
        ],
    ),
    (
        "43",
        "충청북도",
        "North Chungcheong",
        1187,
        [
            ("박기운", "朴己云", D, 113859, 1),
            ("송필만", "宋必滿", D, 109725, 1),
            ("박찬희", "朴贊熙", D, 106783, 2),
            ("오범수", "吳範秀", L, 105346, 2),
        ],
    ),
    (
        "44",
        "충청남도",
        "South Chungcheong",
        1187,
        [
            ("이범석", "李範奭", None, 267391, 1),
            ("심종석", "沈宗錫", D, 196747, 1),
            ("이범승", "李範昇", D, 158315, 1),
            ("이훈구", "李勳求", S, 146059, 2),
            ("정긍모", "鄭競謨", None, 125148, 2),
            ("한광석", "韓光錫", L, 108908, 2),
        ],
    ),
    (
        "45",
        "전라북도",
        "North Jeolla",
        1188,
        [
            ("소선규", "蘇宣奎", D, 264695, 1),
            ("강택수", "姜澤秀", D, 197071, 1),
            ("송방용", "宋邦鏞", None, 165206, 1),
            ("양춘근", "梁春根", None, 163181, 2),
            ("엄병학", "嚴秉學", D, 139983, 2),
            ("엄민영", "嚴敏永", D, 133459, 2),
        ],
    ),
    (
        "46",
        "전라남도",
        "South Jeolla",
        1188,
        [
            ("황성수", "黃聖秀", L, 260181, 1),
            ("조국현", "曺國鉉", D, 248273, 1),
            ("김남중", "金南中", None, 235959, 1),
            ("이남규", "李南圭", D, 210282, 1),
            ("박철웅", "朴哲雄", None, 195036, 2),
            ("정문갑", "丁文甲", None, 180512, 2),
            ("최상채", "崔相彩", D, 174460, 2),
            ("양회영", "梁會英", D, 161178, 2),
        ],
    ),
    (
        "47",
        "경상북도",
        "North Gyeongsang",
        1189,
        [
            ("백남억", "白南檍", D, 214300, 1),
            ("이효상", "李孝祥", None, 192167, 1),
            ("송관수", "宋寬洙", None, 184794, 1),
            ("최희송", "崔熙松", D, 174393, 1),
            ("이원만", "李源萬", D, 165600, 2),
            ("권동철", "權東哲", None, 154437, 2),
            ("김장섭", "金長涉", None, 154053, 2),
            ("최달희", "崔達熙", K, 135160, 2),
        ],
    ),
    (
        "48",
        "경상남도",
        "South Gyeongsang",
        1189,
        [
            ("안호상", "安浩相", None, 410328, 1),
            ("오위영", "吳緯泳", D, 370202, 1),
            ("김용주", "金龍周", D, 309284, 1),
            ("설창수", "薛昌洙", D, 267320, 1),
            ("윤치형", "尹致衡", D, 236328, 2),
            ("김형두", "金炯斗", None, 228529, 2),
            ("정상구", "鄭相九", R, 188792, 2),
            ("김달범", "金達範", None, 176084, 2),
        ],
    ),
    (
        "42",
        "강원도",
        "Gangwon",
        1190,
        [
            ("정순응", "鄭順膺", D, 132592, 1),
            ("김대식", "金大植", None, 127434, 1),
            ("김병노", "金秉魯", D, 114976, 2),
            ("김진구", "金振九", D, 110973, 2),
        ],
    ),
    (
        "49",
        "제주도",
        "Jeju",
        1190,
        [
            ("강재량", "康才良", D, 23257, 1),
            ("강경옥", "康慶玉", None, 21362, 2),
        ],
    ),
]
STATS: dict[str, tuple[int, int, int, int]] = {
    "11": (1109569, 813403, 19277, 832680),
    "41": (1297896, 1066700, 44046, 1110746),
    "43": (646631, 547885, 18651, 566536),
    "44": (1169383, 956086, 38061, 994147),
    "45": (1105114, 900952, 44112, 945064),
    "46": (1616626, 1297554, 72008, 1369562),
    "47": (1801826, 1439360, 85438, 1524798),
    "48": (1940109, 1535074, 64754, 1599828),
    "42": (769474, 664694, 18541, 683235),
    "49": (136804, 115997, 5095, 121092),
}
STATS_TOTAL = (11593432, 9337705, 409983, 9747688)
STATS_PAGE = 1191
SOURCE_KEY = "book"
REGISTRY = f"{SOURCE}/{DAY}"
PAGE_NUMBERS = sorted({row[3] for row in PROVINCES} | {STATS_PAGE})


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [REGISTRY]


def fetch() -> None:
    for number in PAGE_NUMBERS:
        raw.fetch(SOURCE, PAGE_IMAGE.format(number), suffix=".jpg")


def database() -> dict[str, object]:
    with connect() as conn:
        polity = conn.execute("select id from polity where iso_code = 'KR'").fetchone()
        election = conn.execute(
            "select entity_id from slug where path = 'kr/1960-07-29-legislative'",
        ).fetchone()
        units = {
            code: (str(unit), str(area))
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'kr.sido'"
            ).fetchall()
        }
    assert polity is not None and election is not None
    return {"polity": str(polity[0]), "election": str(election[0]), "units": units}


def build() -> Package:
    for code, _, _, _, rows in PROVINCES:
        electors, valid, invalid, voters = STATS[code]
        assert valid + invalid == voters, code
        assert sum(1 for row in rows if row[4] == 1) == (len(rows) + 1) // 2, code
        assert all(a[3] > b[3] for a, b in pairwise(rows) if a[4] == b[4]), code
    assert [sum(STATS[code][index] for code in STATS) for index in range(4)] == list(STATS_TOTAL)
    assert sum(len(province[4]) for province in PROVINCES) == 58
    db = database()
    polity, election = str(db["polity"]), str(db["election"])
    units = db["units"]
    assert isinstance(units, dict)
    p = Package(
        REGISTRY,
        SOURCE,
        "The House of Councillors election of 29 July 1960: 10 province-wide districts and 58 elected councillors, "
        "from the National Election Commission's history of elections",
    )
    p.external |= {polity, election, *(str(units[code][0]) for code in STATS), *(str(units[code][1]) for code in STATS)}
    source = key("source", SOURCE_KEY)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=BOOK + "/index.html",
        license=LICENSE,
        license_url="https://library.nec.go.kr/",
        attribution="중앙선거관리위원회, 대한민국선거사 제1집",
    )
    for number in PAGE_NUMBERS:
        p.artifact(raw.cached(SOURCE, PAGE_IMAGE.format(number)), source)
    body = key("body", "councillors")
    p.add("body", id=body, polity_id=polity, kind="upper_house")
    p.name(body, "House of Councillors", "en", "english")
    p.name(body, "참의원", "ko", "official", False)
    system = key("electoral_system", "councillors-1960")
    p.add(
        "electoral_system",
        id=system,
        polity_id=polity,
        valid_from=DAY,
        valid_to=date(1961, 5, 16),
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", system),
        entity_id=system,
        kind="methodology",
        body_md=(
            "Each province was one district electing 2 to 8 councillors at once. A voter marked up to half the "
            "district's seats (3 of 6, for example) among the listed candidates, and the candidates with the most "
            "votes won (the National Election Commission's cyber election history, on the 1960 election)."
        ),
        lang="en",
    )
    parties = {name: key("party", name) for name in PARTIES}
    for name, (ko, en) in PARTIES.items():
        p.add("party", id=parties[name], kind="party", polity_id=polity)
        p.name(parties[name], ko, "ko", "official")
        p.name(parties[name], en, "en", "english", False)
    for code, ko, en, _, rows in PROVINCES:
        contest = key("contest", code)
        unit, area = units[code]
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=len(rows),
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"House of Councillors, {en}", "en", "english")
        p.name(contest, f"참의원의원 {ko}", "ko", "official", False)
        p.add("slug", path=f"kr/1960-07-29-legislative/councillors-{slug(en)}", entity_id=contest, is_current=True)
        result_set = key("result_set", code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        page = next(row[3] for row in PROVINCES if row[0] == code)
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=source,
            locator=f"printed pages {page - 1} and {STATS_PAGE - 1}",
        )
        electors, valid, invalid, voters = STATS[code]
        cells = {
            "electors": (REGISTERED, electors),
            "voters": (BALLOTS, voters),
            "valid": (VALID, valid),
            "invalid": (INVALID, invalid),
        }
        for name, (measure, value) in cells.items():
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for number, (hangul, hanja, party, votes, term) in enumerate(rows, 1):
            tag = f"{code}:{number}"
            option = key("option", tag)
            label = f"{hangul} ({hanja})"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=parties[party] if party is not None else None,
                ballot_label=label,
            )
            person = key("person", tag)
            p.add("person", id=person)
            p.name(person, label, "ko", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=parties[party] if party is not None else None,
                is_independent=party is None,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=votes,
            )
    p.accept(
        "accounting.votes_valid",
        "Each voter marked up to half of the province's seats, so the elected candidates' votes add up to more "
        "than the valid ballots; the book prints no votes for candidates who lost.",
    )
    return p


def parse() -> None:
    fetch()
    build().write()


def emit() -> None:
    package.run("load", packages())
