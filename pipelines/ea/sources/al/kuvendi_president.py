"""Albania's presidential elections by the Assembly, from the Assembly's minutes and decisions on parlament.al.

The Assembly publishes the minutes (procesverbale) of its plenary sessions and its decisions from 2013 on. They record
the secret ballot for President as the Secretariat for Procedures and Votes read it out: the members who took part,
the valid and invalid ballots, and the votes for the candidate, against, and abstaining. The elections of 2017 and 2022
each went to a fourth ballot, because no candidate was nominated for the first three, which the Assembly treats as
held and without votes (Constitution, article 87(2)). Only the fourth ballot of each election has votes.
"""

import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "al.kuvendi_president"
ALBANIA = str(uuid5(package.NAMESPACE, "polity\x1fAL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fALB"))
SCHEME = "al.nation"
HOME = "https://www.parlament.al/"
FILES = "https://kuvendiwebfiles.blob.core.windows.net/webfiles/"
VOTES, BALLOTS, VALID, INVALID = 1, 3, 4, 5
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


ASSEMBLY = key("source", "kuvendi")
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president")


@dataclass(frozen=True)
class Vote:
    day: date
    candidate: str
    nominators: int
    group: str  # the parliamentary group the nominators belonged to, as the record names it
    took_part: int
    pro: int
    against: int
    abstained: int
    invalid: int
    minutes: str  # the plenary minutes that print the result
    others: tuple[str, ...]  # minutes and decisions that record the earlier ballots or the decision
    needles: tuple[str, ...]  # phrases the minutes must contain, after whitespace is collapsed


VOTES_HELD = [
    Vote(
        date(2017, 4, 28),
        "Ilir Meta",
        23,
        "",
        89,
        87,
        2,
        0,
        0,
        FILES + "Seanca-e-dat--s-28.4.2017-pasdite.pdf",
        (FILES + "Procesverbal-dat---27.04.2017.pdf", FILES + "vendim-nr-53-dt-28-4-2017.pdf"),
        ("kanë marrë pjesë në votim 89 deputetë", "mori pro 87 vota, kundër 2 vota, asnjë abstenim"),
    ),
    Vote(
        date(2022, 6, 4),
        "Bajram Begaj",
        37,
        "Partia Socialiste",
        83,
        78,
        4,
        1,
        0,
        FILES + "20220607085517Proc.%20dt%204.6.2022.pdf",
        (FILES + "20220607085146Proc.%20dt%2030.5.2022%20seanca%20e%20Presidentit.pdf",),
        ("83 deputetë kanë marrë pjesë në votim", "78 vota pro, 4 kundër dhe 1 abstenim"),
    ),
]

METHOD = (
    "The Assembly's secret ballot for President, as the Secretariat for Procedures and Votes read the result in the "
    "plenary session recorded in the minutes. The members who took part and the valid and invalid ballots are the "
    "Secretariat's counts. The Assembly's 140 members vote with one ballot paper each; a ballot is for the candidate, "
    "against, or an abstention, and the abstentions are valid ballots. The candidate is elected on this fourth ballot "
    "by more than half of all the Assembly's members. No candidate was nominated for the first three ballots, so no "
    "votes were taken then."
)


def election_key(v: Vote) -> str:
    return f"{SOURCE}/{v.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(election_key(v) for v in VOTES_HELD)]


def urls(v: Vote) -> list[str]:
    return [v.minutes, *v.others]


def fetch() -> None:
    for v in VOTES_HELD:
        for url in urls(v):
            raw.fetch(SOURCE, url, suffix=".pdf")


def text(url: str) -> str:
    out = subprocess.run(["pdftotext", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True)
    return " ".join(out.stdout.decode("utf-8", "replace").split())


def parse() -> None:
    for v in VOTES_HELD:
        body = text(v.minutes)
        for needle in v.needles:
            if needle not in body:
                raise ValueError(f"{v.day}: the minutes do not contain {needle!r}")
        if v.pro + v.against + v.abstained != v.took_part - v.invalid:
            raise ValueError(f"{v.day}: the votes do not add up to the valid ballots")
        if v.candidate.split()[-1] not in text(v.others[-1]) and v.day.year == 2017:
            raise ValueError(f"{v.day}: the decision does not name {v.candidate}")
    registry().write()
    for v in VOTES_HELD:
        election(v).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=ASSEMBLY,
        kind="official_body",
        publisher="Kuvendi i Republikës së Shqipërisë",
        title="Minutes of the plenary sessions and decisions of the Assembly of the Republic of Albania",
        url=HOME,
        license="No license stated; published by the Assembly for public use",
        attribution="Kuvendi i Republikës së Shqipërisë",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Albania's presidency and the electoral system of its election by the Assembly",
    )
    p.external.add(ALBANIA)
    p.add("scheme", code=SCHEME, polity_id=ALBANIA, kind="administrative", rank=1, noun="nation", code_system=None)
    p.add("office", id=PRESIDENCY, polity_id=ALBANIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Albania", "en", "official")
    p.name(PRESIDENCY, "Presidenti i Republikës së Shqipërisë", "sq", "official", False)
    p.add("electoral_system", id=SYSTEM, polity_id=ALBANIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under article 87 of the Constitution the Assembly elects the President by secret ballot, on the "
        "proposal of a group of at least 20 members. The first three ballots need three fifths of all the "
        "Assembly's members and the fourth, more than half of them. Each ballot after the first is held within "
        "seven days of the failure of the one before, and a ballot counts as held even when no candidate is "
        "nominated. If a fifth ballot fails, the Assembly is dissolved.",
        lang="en",
    )
    return p


def election(v: Vote) -> Package:
    day = v.day
    p = Package(
        election_key(v),
        SOURCE,
        f"The election of Albania's President by the Assembly on {day:%-d %B %Y}, from the Assembly's minutes",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ALBANIA, COUNTRY}
    _source(p)
    p.artifact(raw.cached(SOURCE, v.minutes), ASSEMBLY)
    for url in v.others:
        p.artifact(raw.cached(SOURCE, url), ASSEMBLY)
    id = key("election", day)
    path = f"al/{day}-president"
    p.add("election", id=id, polity_id=ALBANIA, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} Albanian presidential election", "en", "official")
    p.name(id, f"Zgjedhja e Presidentit të Republikës, {day.year}", "sq", "native", False)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=4,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{day.year} presidential election, fourth ballot", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ASSEMBLY,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    nominated = (
        f" The candidate was nominated by {v.nominators} members"
        + (f" of the {v.group} parliamentary group" if v.group else "")
        + "."
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD + nominated,
        lang="en",
    )
    person = key("person", day)
    p.add("person", id=person)
    p.name(person, v.candidate, "sq", "official")
    option = key("option", day, "candidate")
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
        id=key("candidacy", day),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=v.candidate,
        is_independent=False,
    )
    against = key("option", day, "against")
    p.add(
        "contest_option",
        id=against,
        contest_id=contest,
        kind="none_of_the_above",
        ballot_position=2,
        ballot_label="Against",
    )
    abstain = key("option", day, "abstain")
    p.add(
        "contest_option",
        id=abstain,
        contest_id=contest,
        kind="none_of_the_above",
        ballot_position=3,
        ballot_label="Abstention",
    )
    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=1)
    unit = key("unit", day)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ALBANIA,
        scheme_code=SCHEME,
        code="AL",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    rows: list[tuple[str | None, int, int]] = [
        (option, VOTES, v.pro),
        (against, VOTES, v.against),
        (abstain, VOTES, v.abstained),
        (None, BALLOTS, v.took_part),
        (None, VALID, v.took_part - v.invalid),
        (None, INVALID, v.invalid),
    ]
    for opt, measure, n in rows:
        p.add("declared", result_set_id=result_set, option_id=opt, measure=measure, value=n)
    p.frame(
        "result_value",
        pl.DataFrame(
            [(unit, opt, measure, n) for opt, measure, n in rows],
            schema={"unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        ),
    )
    return p
