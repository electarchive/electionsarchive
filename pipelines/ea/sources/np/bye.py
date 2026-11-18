"""Election Commission of Nepal: the 2081 Baishakh 15 (2024-04-27) by-elections, House of Representatives
Ilam 2 and Provincial Assembly Bajhang 1(1), from the vote charts the commission's results portal embeds."""

import json
import re
from datetime import date
from typing import Any

from ea import raw
from ea.package import Package
from ea.sources.np.eci import (
    COUNTRY,
    FPTP,
    HOR,
    NCOMMISSION,
    NEPAL,
    PORTAL,
    SOURCE,
    area,
    key,
    party,
)

DAY = "2024-04-27"
PAGE = PORTAL + "ElectionResultCentral2081bi.aspx"
ILAM = "https://flo.uri.sh/visualisation/17746262/embed"
BAJHANG = "https://flo.uri.sh/visualisation/17746661/embed"
SUDURPASHCHIM = 7

# The charts name each candidate's party only by its election symbol; these three symbols belong to parties the
# 2022 results already name.
SYMBOLS = {
    "uml": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "nc": "नेपाली काँग्रेस",
    "mc": "नेपाल कम्युनिष्ट पार्टी (माओवादी केन्द्र)(एकल चुनाव चिन्ह)",
}
METHOD = (
    "Counted by the Election Commission, Nepal, and published on its results portal: the votes each candidate "
    "received, the candidate marked as elected, and the registered voters, votes cast, valid votes, and invalid "
    "votes of the constituency. The portal names a candidate's party only through the election symbol; the "
    "symbols of the Nepali Congress, the Communist Party of Nepal (UML), and the Communist Party of Nepal "
    "(Maoist Centre) are linked to those parties, and the other candidates carry no party here."
)

CONTESTS = [
    {
        "tag": "hor:3-2",
        "level": "House of Representatives",
        "en": "Ilam 2",
        "ne": "प्रतिनिधि सभा निर्वाचन, इलाम - २",
        "slug": "house/ilam-2",
        "body": HOR,
        "scheme": "np.hor",
        "code": "3-2",
        "url": ILAM,
        "totals": {2: 115889, 3: 69374, 4: 68472, 5: 902},
    },
    {
        "tag": "pa:69-1-1",
        "level": "Provincial Assembly",
        "en": "Bajhang 1(1)",
        "ne": "प्रदेश सभा निर्वाचन, बझाङ - १(१)",
        "slug": "province/bajhang-1-1",
        "body": key("body", "provincial_assembly", SUDURPASHCHIM),
        "scheme": "np.pa",
        "code": "69-1-1",
        "url": BAJHANG,
        "totals": {2: 61748, 3: 30337, 4: 29705, 5: 632},
    },
]


def fetch() -> None:
    for url in (PAGE, ILAM, BAJHANG):
        raw.fetch(SOURCE, url, suffix=".html")


def chart(url: str) -> list[dict[str, Any]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8")
    found = re.search(r"_Flourish_data = (\{.*?\}\]\}),\n", text)
    if found is None:
        raise ValueError(f"no chart data in {url}")
    return [r for r in json.loads(found.group(1))["data"] if r["label"]]


def symbol(image: str) -> str:
    return image.rsplit("/", 1)[-1].removesuffix(".jpg")


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2024-04-27 Nepal by-elections, House of Representatives Ilam 2 and Provincial Assembly Bajhang 1(1), "
        "from the Election Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEPAL, COUNTRY}
    p.artifact(raw.cached(SOURCE, PAGE), NCOMMISSION)
    el = key("election", DAY)
    path = f"np/{DAY}-by-election"
    p.add(
        "election",
        id=el,
        polity_id=NEPAL,
        kind="by_election",
        date_from=date.fromisoformat(DAY),
        date_to=date.fromisoformat(DAY),
        original_text="२०८१ वैशाख १५",
        status="held",
    )
    p.name(el, "2024 Nepal by-election", "en", "official")
    p.add("slug", path=path, entity_id=el, is_current=True)
    for cfg in CONTESTS:
        tag = str(cfg["tag"])
        rows = chart(str(cfg["url"]))
        p.artifact(raw.cached(SOURCE, str(cfg["url"])), NCOMMISSION)
        c = key("contest", DAY, tag)
        p.add(
            "contest",
            id=c,
            election_id=el,
            kind="body_seats",
            body_id=cfg["body"],
            district_area_id=area(str(cfg["scheme"]), str(cfg["code"])),
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        p.name(c, f"2024 {cfg['level']} election, {cfg['en']}", "en", "official")
        p.name(c, str(cfg["ne"]), "ne", "native", False)
        p.add("slug", path=f"{path}/{cfg['slug']}", entity_id=c, is_current=True)
        rs = key("result_set", DAY, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=NCOMMISSION,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", DAY, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEPAL,
            scheme_code=str(cfg["scheme"]),
            code=f"{DAY}:{cfg['code']}",
            area_relation="equals",
            area_id=area(str(cfg["scheme"]), str(cfg["code"])),
            kind="standard",
        )
        for measure, value in dict(cfg["totals"]).items():  # type: ignore[arg-type]
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
            p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=value)
        total = 0
        winners = 0
        for i, r in enumerate(rows):
            name = " ".join(str(r["label"]).replace("*", "").split())
            sym = symbol(str(r["image"]))
            member = party(SYMBOLS[sym]) if sym in SYMBOLS else None
            option = key("option", DAY, tag, i)
            person = key("person", DAY, tag, i)
            p.add("person", id=person)
            p.name(person, name, "ne", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=c,
                kind="candidate",
                party_id=member,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", DAY, tag, i),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=member,
                is_independent=False,
            )
            votes = int(r["values"][0])
            total += votes
            p.add(
                "result_value",
                result_set_id=rs,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
            if r["category"] == "निर्वाचित":
                winners += 1
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
        if winners != 1:
            raise ValueError(f"{tag} has {winners} elected candidates")
        if total != dict(cfg["totals"])[4]:  # type: ignore[arg-type]
            raise ValueError(f"{tag} candidate votes {total} differ from the printed valid votes")
    return p


def parse() -> None:
    election().write()
