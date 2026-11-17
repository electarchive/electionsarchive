"""Bangladesh's presidential elections by Parliament, from the gazettes of the Election Commission.

The Commission's presidential result page links one gazette for each election it declared: the 2018, 2022, and 2026
gazettes, each of which declares the sole valid nominee elected under section 7 or 11 of the Presidential Election Act
1991. No poll was held and the gazettes print no votes. The page links nothing for the three direct elections of 1978,
1981, and 1986 or for the other elections since 1991.
"""

import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.bd.bijoy import unicode

SOURCE = "bd.ecs_president"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
HOME = "https://www.ecs.gov.bd/"
ASSET = "https://asset.ecs.gov.bd/media/uploaded_files/"
FILES = {
    2018: ASSET + "package/filemanager/2022/08/President 2018.pdf",
    2023: ASSET + "package/filemanager/2023/02/Presidential elections Gadzed in Bangladesh - 2022.pdf",
    2026: ASSET + "notices/2026/08/fed85d13-4b8f-4b1e-9b5d-6d88a4f16316.pdf",
}


@dataclass(frozen=True)
class Declaration:
    day: date
    english: str
    bengali: str
    home: str
    gazette: str
    law: str


# The date is the gazette's: the Commission declares the sole nominee elected by notice, and the gazettes name no
# other date. The 2023 gazette sits under the Commission's heading "2022", for the election cycle that began then.
DECLARATIONS = {
    2018: Declaration(
        date(2018, 2, 7), "Md. Abdul Hamid", "মোঃ আবদুল হামিদ", "Kishoreganj", "17.00.0000.034.34.005.18-71", "7"
    ),
    2023: Declaration(
        date(2023, 2, 13), "Md. Shahabuddin", "মোঃ সাহাবুদ্দিন", "Pabna", "17.00.0000.034.34.025.22-119", "7"
    ),
    2026: Declaration(
        date(2026, 8, 20),
        "Mirza Fakhrul Islam Alamgir",
        "মির্জা ফখরুল ইসলাম আলমগীর",
        "Thakurgaon",
        "17.00.34.035.46.055.18-332",
        "11",
    ),
}

METHOD = (
    "The Bangladesh Election Commission gazetted that the only valid nominee was elected President under the "
    "Presidential Election Act 1991 and Rule 12(6) of the Presidential Election Rules 1991, so Parliament cast no "
    "votes. The gazette prints no vote counts, and none are stored. The election's date is the gazette's."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECS = key("source", "ecs")
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{d.day}" for d in DECLARATIONS.values())]


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    for year in (2018, 2023):
        text = unicode(
            subprocess.run(
                ["pdftotext", str(raw.cached(SOURCE, FILES[year]).path), "-"], check=True, capture_output=True
            ).stdout.decode("utf-8", "replace")
        )
        text = " ".join(text.split())
        spec = DECLARATIONS[year]
        if spec.bengali not in text:
            raise ValueError(f"{year}: the gazette does not name {spec.bengali}")
    registry().write()
    for spec in DECLARATIONS.values():
        election(spec).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=ECS,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Gazettes declaring the election of the President",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bangladesh's presidency and the electoral system of its election by Parliament",
    )
    p.external.add(BANGLADESH)
    p.add("office", id=PRESIDENCY, polity_id=BANGLADESH, kind="head_of_state")
    p.name(PRESIDENCY, "President of Bangladesh", "en", "official")
    p.name(PRESIDENCY, "গণপ্রজাতন্ত্রী বাংলাদেশের রাষ্ট্রপতি", "bn", "official", False)
    p.add("electoral_system", id=SYSTEM, polity_id=BANGLADESH, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under Article 48(1) of the Constitution the members of Parliament elect the President. When only "
        "one valid nomination remains, the returning officer declares that nominee elected and no vote is taken.",
        lang="en",
    )
    return p


def election(spec: Declaration) -> Package:
    day = spec.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The election of Bangladesh's President declared on {day:%-d %B %Y}, from the Election Commission's gazette",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(BANGLADESH)
    _source(p)
    year = next(y for y, d in DECLARATIONS.items() if d is spec)
    p.artifact(raw.cached(SOURCE, FILES[year]), ECS)
    id = key("election", day)
    path = f"bd/{day}-president"
    p.add("election", id=id, polity_id=BANGLADESH, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} Bangladeshi presidential election", "en", "official")
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="tacit",
    )
    p.name(contest, f"{day.year} presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
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
        completeness=0.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    person = key("person", day)
    p.add("person", id=person)
    p.name(person, spec.english, "en", "official")
    p.name(person, spec.bengali, "bn", "official", False)
    option = key("option", day)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        ballot_position=1,
        ballot_label=spec.english,
    )
    p.add(
        "candidacy",
        id=key("candidacy", day),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=spec.english,
        is_independent=False,
    )
    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=1)
    return p
