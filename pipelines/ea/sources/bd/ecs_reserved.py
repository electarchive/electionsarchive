"""Bangladesh's reserved women's seats in the Jatiya Sangsad, from the Election Commission's gazettes and notices.

The Jatiya Sangsad (Reserved Women's Seats) Election Act 2004 gives each party and alliance a share of the reserved
seats in proportion to its general seats, and the members of Parliament elect the women. When a party or alliance
nominates no more women than it has seats, the Commission declares them elected under section 26(2) and no vote is
taken. The Commission's portal links the gazette that declared the 49 women elected for the 13th Parliament on
30 April 2026, a correction of one name, the notice that filled the one remaining seat on 2 November 2026, and two
notices of the 12th Parliament's candidates in February 2024. It links no declaration for the 12th Parliament and
nothing for the earlier Parliaments.
"""

from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "bd.ecs_reserved"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
HOME = "https://www.ecs.gov.bd/"
ASSET = "https://asset.ecs.gov.bd/media/uploaded_files/notices/"

# The 12th Parliament's notices of the validly nominated candidates (22 Feb 2024) and the final list (27 Feb 2024).
NOMINATED_2024 = ASSET + "2024/02/QK4OhbpFtBiaOyH7uvsr7QPmoaCHgB5MrvGEw73T.pdf"
FINAL_2024 = ASSET + "2024/02/xx5eQWPvCCFnib47h8ZFeGKz8Jt7KBOvvZo6ec1H.pdf"
# The 13th Parliament's gazette of 30 April 2026, the correction of 15 September 2026, and the notice of 2 November.
GAZETTE_2026 = ASSET + "2026/05/6e446992-6e94-497e-8b08-6bd54c2f4a26.pdf"
CORRECTION_2026 = ASSET + "2026/09/67c4d10a-e239-4caf-aadb-1cf0f401bc8e.pdf"
BYPOLL_2026 = ASSET + "2026/11/02c985f9-9287-4a9b-8c0c-f5527a70d797.pdf"
FILES = [NOMINATED_2024, FINAL_2024, GAZETTE_2026, CORRECTION_2026, BYPOLL_2026]

AL, JP, BNP, JAMAAT = (
    "Bangladesh Awami League",
    "Jatiya Party",
    "Bangladesh Nationalist Party",
    "Bangladesh Jamaat-e-Islami",
)
PARTIES = {
    AL: "বাংলাদেশ আওয়ামী লীগ",
    JP: "জাতীয় পার্টি",
    BNP: "বাংলাদেশ জাতীয়তাবাদী দল",
    JAMAAT: "বাংলাদেশ জামায়াতে ইসলামী",
}

# The women of the 13th Parliament in the gazette's order, by party or alliance. The correction of 15 September 2026
# changed the 28th name from "মমতাজ আলো" to "মমতাজ আলো আহমেদ".
BNP_2026 = [
    "সেলিমা রহমান",
    "শিরীন সুলতানা",
    "রাশেদা বেগম হীরা",
    "রেহানা আক্তার রানু",
    "নেওয়াজ হালিমা আরলী",
    "মোসাঃ ফরিদা ইয়াসমিন",
    "বিলকিস ইসলাম",
    "সাকিলা ফারজানা",
    "হেলেন জেরিন খান",
    "নিলোফার চৌধুরী মনি",
    "নিপুন রায় চৌধুরী",
    "জীবা আমিনা খান",
    "মাহমুদা হাবীবা",
    "মোছাঃ সাবিরা সুলতানা",
    "সানসিলা জেবরিন",
    "সানজিদা ইসলাম",
    "সুলতানা আহমেদ",
    "ফাহমিদা হক",
    "আল্লা মিন্জ",
    "সুবর্ণা সিকদার",
    "শামীম আরা বেগম স্বপ্না",
    "মোসাম্মৎ শাম্মী আক্তার",
    "ফেরদৌসী আহমেদ",
    "বীথিকা বিনতে হোসাইন",
    "মোছাঃ সুরাইয়া জেরিন",
    "মানসুরা আক্তার",
    "জহরত আদিব চৌধুরী",
    "মমতাজ আলো আহমেদ",
    "ফাহিমা নাসরিন",
    "আরিফা সুলতানা",
    "মোছাঃ সানজিদা ইয়াসমিন",
    "নাদিয়া পাঠান পাপন",
    "শওকত আরা আক্তার",
    "মাধবী মার্মা",
    "সেলিনা সুলতানা",
    "রেজেকা সুলতানা",
]
JAMAAT_2026 = [
    "নুরুন্নিসা সিদ্দীকা",
    "মারজিয়া বেগম",
    "সাবিকুন্নাহার",
    "মোসাঃ নাজমুন নাহার",
    "মাহফুজা হান্নান",
    "মিসেস সাজেদা সামাদ",
    "শামছুন্নাহার বেগম",
    "মারদিয়া মমতাজ",
    "রোকেয়া বেগম",
    "মাহমুদা আলম মিতু",
    "তাসমিয়া প্রধান",
    "মাহবুবা হাকিম",
]
INDEPENDENTS_2026 = ["সুলতানা জেসমিন"]
BYPOLL_2026_NAMES = ["এলিজা জামান"]


@dataclass(frozen=True)
class Group:
    """A party or alliance's allocation: its party, the label the Commission gives it, and the women declared."""

    party: str | None
    label: str
    women: list[str]
    seats: int


@dataclass(frozen=True)
class Election:
    day: date
    number: int
    kind: str
    magnitude: int
    groups: list[Group]
    files: list[str]
    method: str


METHOD_2024 = (
    "The Commission's notice of 27 February 2024 publishes the final list of 50 candidates for the 50 reserved "
    "seats: 48 nominated by the Bangladesh Awami League and its alliance and 2 by the Jatiya Party. No candidate "
    "withdrew by the deadline, so the number of candidates equals the number of seats. Seats are stored as the "
    "candidates each group put forward. The portal holds no gazette declaring them elected, and the candidates' "
    "names are not stored. The date is the notice's."
)
METHOD_2026 = (
    "The Commission's gazette of 30 April 2026 declares 49 women elected to the reserved seats under section 26(2) "
    "of the 2004 Act, by party and alliance: 36 for the Bangladesh Nationalist Party and its alliance, 12 for "
    "Bangladesh Jamaat-e-Islami and its alliance, and 1 for the alliance of six independent members. One of the 50 "
    "seats stayed vacant and was filled on 2 November 2026. No poll was held and no votes are stored. The "
    "Commission's notice of 15 September 2026 corrected the 28th name, and the corrected name is stored. The date is "
    "the gazette's."
)
METHOD_BYPOLL = (
    "The Commission's notice of 2 November 2026 declares one woman elected under section 26(2) of the 2004 Act to "
    "the reserved seat left vacant by the Bangladesh Nationalist Party and its alliance. No poll was held. The date "
    "is the notice's."
)

ELECTIONS = [
    Election(
        date(2024, 2, 27),
        12,
        "general",
        50,
        [
            Group(AL, "বাংলাদেশ আওয়ামী লীগ ও জোট", [], 48),
            Group(JP, "জাতীয় পার্টি", [], 2),
        ],
        [NOMINATED_2024, FINAL_2024],
        METHOD_2024,
    ),
    Election(
        date(2026, 4, 30),
        13,
        "general",
        50,
        [
            Group(BNP, "বাংলাদেশ জাতীয়তাবাদী দল- বিএনপি ও জোট", BNP_2026, 36),
            Group(JAMAAT, "বাংলাদেশ জামায়াতে ইসলামী ও জোট", JAMAAT_2026, 12),
            Group(None, "স্বতন্ত্র ০৬ (ছয়) জন সংসদ সদস্যের সমন্বয়ে গঠিত জোট", INDEPENDENTS_2026, 1),
        ],
        [GAZETTE_2026, CORRECTION_2026],
        METHOD_2026,
    ),
    Election(
        date(2026, 11, 2),
        13,
        "by_election",
        1,
        [Group(BNP, "বাংলাদেশ জাতীয়তাবাদী দল- বিএনপি ও জোট", BYPOLL_2026_NAMES, 1)],
        [BYPOLL_2026],
        METHOD_BYPOLL,
    ),
]
ORDINAL = {12: "12th", 13: "13th"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECS = key("source", "ecs")
SANGSAD = key("body", "jatiya-sangsad")
SYSTEM = key("electoral_system", "reserved-women")


def party(name: str) -> str:
    return key("party", name.casefold())


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    for spec in ELECTIONS:
        election(spec).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=ECS,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Gazettes and notices of the reserved women's seats election",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Jatiya Sangsad, the parties that won reserved women's seats, and the electoral system of those seats",
    )
    p.external.add(BANGLADESH)
    p.add("body", id=SANGSAD, polity_id=BANGLADESH, kind="unicameral")
    p.name(SANGSAD, "Jatiya Sangsad", "en", "official")
    p.name(SANGSAD, "জাতীয় সংসদ", "bn", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=BANGLADESH, rules={"formula": "hare"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under the Jatiya Sangsad (Reserved Women's Seats) Election Act 2004 the reserved seats are shared "
        "among the parties and alliances in proportion to the general seats their members hold, and the members "
        "of Parliament elect the women each group nominates. A group that nominates no more women than it has "
        "seats has them declared elected without a vote.",
        lang="en",
    )
    for name, bengali in PARTIES.items():
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=BANGLADESH)
        p.name(id, name, "en", "official")
        p.name(id, bengali, "bn", "official", False)
    return p


def election(spec: Election) -> Package:
    day = spec.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The reserved women's seats of Bangladesh's {ORDINAL[spec.number]} Jatiya Sangsad declared on "
        f"{day:%-d %B %Y}, from the Election Commission's gazette or notice",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(BANGLADESH)
    _source(p)
    for url in spec.files:
        p.artifact(raw.cached(SOURCE, url), ECS)
    id = key("election", day)
    path = f"bd/{day}-reserved-women"
    p.add("election", id=id, polity_id=BANGLADESH, kind=spec.kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    title = (
        f"{spec.day.year} Bangladeshi reserved women's seats by-election"
        if spec.kind == "by_election"
        else f"{day.year} Bangladeshi reserved women's seats election"
    )
    p.name(id, title, "en", "official")
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=SANGSAD,
        magnitude=spec.magnitude,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="tacit",
    )
    p.name(contest, title, "en", "official")
    p.add("slug", path=f"{path}/reserved-women", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="tacit",
        source_id=ECS,
        status="active",
        is_default=True,
        completeness=sum(g.seats for g in spec.groups) / spec.magnitude,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=spec.method, lang="en")
    for index, group in enumerate(spec.groups, start=1):
        option = key("option", day, index)
        solo = group.party is None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="independents" if solo else "list",
            party_id=None if solo else party(group.party or ""),
            ballot_position=index,
            ballot_label=group.label,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="won", seats=group.seats)
        for place, name in enumerate(group.women, start=1):
            person = key("person", day, index, place)
            p.add("person", id=person)
            p.name(person, name, "bn", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, index, place),
                person_id=person,
                option_id=option,
                role="list_member",
                ballot_name=name,
                member_of_party_id=None if solo else party(group.party or ""),
                is_independent=solo,
            )
    return p
