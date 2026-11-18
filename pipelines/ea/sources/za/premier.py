"""South Africa's elections of provincial Premiers by the provincial legislatures, from each legislature's Hansard.

A provincial legislature elects its Premier from among its members at its first sitting after a general election and
whenever the office falls vacant (Constitution s. 128, and Part A of Schedule 3 applied to the provinces). A judge
designated by the Chief Justice presides and calls for nominations. With one nomination the judge declares the nominee
elected and no vote is taken. With more, the members vote by secret ballot and the judge announces the votes for each
candidate. The legislature's Hansard records the nominations and the announcement.

Only the Western Cape Provincial Parliament publishes its sitting-by-sitting Hansard in a form this pipeline can reach;
its list of sittings goes back to 2013 but leaves out the first sittings of 2014 and 2019, whose bound volumes no
longer resolve. The other legislatures' records are not online or refuse automated requests.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "za.premier"
SOUTH_AFRICA = str(uuid5(package.NAMESPACE, "polity\x1fZA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fZAF"))
SCHEME = "za.province"
VOTES, INVALID = 1, 5
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


@dataclass(frozen=True)
class Province:
    code: str  # the IEC's province code
    name: str
    legislature: str  # the legislature's own name, for the source
    publisher: str
    home: str


PROVINCES = {
    "WC": Province(
        "WC",
        "Western Cape",
        "Western Cape Provincial Parliament",
        "Western Cape Provincial Parliament",
        "https://www.wcpp.gov.za/?q=hansardview",
    ),
}


@dataclass(frozen=True)
class Candidate:
    name: str  # as the presiding judge's announcement prints it
    votes: int | None
    needle: str  # a phrase of the Hansard that proves the vote or nomination, matched on whitespace-folded text


@dataclass(frozen=True)
class Election:
    province: str
    day: date
    url: str
    candidates: tuple[Candidate, ...]
    winner: str
    invalid: int | None
    invalid_needle: str | None
    note: str


ELECTIONS = (
    Election(
        "WC",
        date(2024, 6, 13),
        "https://www.wcpp.gov.za/sites/default/files/hansarddocs/Hansard%2013.06.2024%20revised%20edited.pdf",
        (
            Candidate("Mr A R Winde", 26, "Mr A R Winde – 26 votes"),
            Candidate("Mr M K Sayed", 14, "Mr M K Sayed – 14 votes"),
        ),
        "Mr A R Winde",
        2,
        "Spoilt papers – 2 votes",
        "The Hansard of the first sitting of the seventh Provincial Parliament records two nominations, Mr Winde's by "
        "Mr T A Simmers and seconded by Mr I H Meyer, and Mr Sayed's by Ms R Windvogel and seconded by Mr F Kamfer, a "
        "secret ballot under item 6 of Part A of Schedule 3 to the Constitution, and the Acting Judge President's "
        "announcement of the votes for each candidate and of 2 spoilt papers. It does not print the number of ballots "
        "cast, so only the candidates' votes and the spoilt papers are stored.",
    ),
)


def plain(name: str) -> str:
    """The name without the honorific the Hansard prints before it."""
    return re.sub(r"^(Mr|Ms|Mrs|Dr|Prof) ", "", name)


def source_id(province: str) -> str:
    return key("source", province)


def office(province: str) -> str:
    return key("office", province)


def system(province: str) -> str:
    return key("electoral_system", province)


def unit(province: str) -> str:
    return key("unit", province)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.province.lower()}-{e.day}" for e in ELECTIONS)]


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, election.url, suffix=Path(election.url).suffix)


def text(path: Path) -> str:
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return " ".join(out.stdout.split())


def check(election: Election) -> None:
    body = text(raw.cached(SOURCE, election.url).path)
    needles = [c.needle for c in election.candidates]
    if election.invalid_needle is not None:
        needles.append(election.invalid_needle)
    for needle in needles:
        if re.sub(r"\s+", " ", needle) not in body:
            raise ValueError(f"{election.province} {election.day}: the Hansard does not print {needle!r}")
    winner = re.sub(r"\s+", " ", election.winner)
    if f"declared {winner} duly elected Premier" not in body:
        raise ValueError(f"{election.province} {election.day}: the Hansard does not declare {winner} elected")


def parse() -> None:
    for election in ELECTIONS:
        check(election)
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The provincial Premiers of South Africa, the sources that record their election, and the electoral system of "
        "a legislature's election of its Premier",
    )
    p.external |= {SOUTH_AFRICA, COUNTRY, SCHEME}
    for code in sorted({e.province for e in ELECTIONS}):
        province = PROVINCES[code]
        id = office(code)
        p.add("office", id=id, polity_id=SOUTH_AFRICA, kind="head_of_government")
        p.name(id, f"Premier of the {province.name}", "en", "official")
        rules = system(code)
        p.add("electoral_system", id=rules, polity_id=SOUTH_AFRICA, rules={"formula": "fptp"}, rules_version=1)
        p.name(rules, f"Election of the Premier by the {province.legislature}", "en", "official")
        p.add(
            "note",
            id=key("note", rules),
            entity_id=rules,
            kind="methodology",
            body_md=f"The {province.legislature} elects the Premier from among its members at its first sitting after "
            "a general election and whenever the office falls vacant (Constitution, section 128). A judge designated "
            "by the Chief Justice presides. When one candidate is nominated, the judge declares that candidate elected "
            "and no vote is taken. When more are nominated, the members vote by secret ballot, and a candidate needs a "
            "majority of the votes cast; otherwise the candidate with the fewest votes drops out and the members vote "
            "again (Part A of Schedule 3).",
            lang="en",
        )
        p.add(
            "source",
            id=source_id(code),
            kind="official_body",
            publisher=province.publisher,
            title=f"Hansard of the {province.legislature}",
            url=province.home,
            license="No license stated; published by the legislature for public use",
            attribution=province.publisher,
        )
        p.add(
            "reporting_unit",
            id=unit(code),
            polity_id=SOUTH_AFRICA,
            scheme_code=SCHEME,
            code=code,
            area_relation="within",
            area_id=COUNTRY,
            kind="aggregate",
        )
    for election in ELECTIONS:
        for candidate in election.candidates:
            person = key("person", election.province, candidate.name)
            p.add("person", id=person)
            p.name(person, plain(candidate.name), "en", "official")
    return p


def build(election: Election) -> Package:
    day, code = election.day, election.province
    province = PROVINCES[code]
    slug = province.name.lower().replace(" ", "-")
    p = Package(
        f"{SOURCE}/{code.lower()}-{day}",
        SOURCE,
        f"The {province.legislature}'s election of the Premier on {day:%-d %B %Y}, from its Hansard",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(SOUTH_AFRICA)
    p.artifact(raw.cached(SOURCE, election.url), source_id(code))
    contested = len(election.candidates) > 1
    id = key("election", code, day)
    path = f"za/{day}-premier-{slug}"
    p.add("election", id=id, polity_id=SOUTH_AFRICA, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} {province.name} premier election", "en", "official")
    contest = key("contest", code, day)
    kind = "counted" if contested else "tacit"
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=office(code),
        magnitude=1,
        round_number=1,
        electoral_system_id=system(code),
        default_result_kind=kind,
    )
    p.name(contest, f"{day.year} {province.name} premier election", "en", "official")
    p.add("slug", path=f"{path}/premier", entity_id=contest, is_current=True)
    result_set = key("result_set", code, day)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind=kind,
        source_id=source_id(code),
        status="active",
        is_default=True,
        completeness=1.0 if contested else 0.0,
    )
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=election.note, lang="en"
    )
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    for position, candidate in enumerate(election.candidates, 1):
        option = key("option", code, day, candidate.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=candidate.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", code, day, candidate.name),
            person_id=key("person", code, candidate.name),
            option_id=option,
            role="head",
            ballot_name=candidate.name,
            is_independent=False,
        )
        if candidate.name == election.winner:
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=1)
        if candidate.votes is not None:
            rows.append((result_set, unit(code), option, VOTES, candidate.votes, 0))
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
    if election.invalid is not None:
        rows.append((result_set, unit(code), None, INVALID, election.invalid, 0))
        p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=election.invalid)
    if rows:
        p.frame(
            "result_value",
            pl.DataFrame(
                rows,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "flags": pl.Int16,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
    return p
