"""South Africa's presidential elections in the National Assembly, 1999-2024, from the Assembly's Hansard.

The Assembly elects the President at its first sitting after a general election and whenever the office falls vacant
(Constitution s. 86 and item 5 and 6 of Part A of Schedule 3). The Chief Justice presides and calls for nominations.
With one nomination he declares the nominee elected and no vote is taken. With more, the members vote by secret ballot
and the Chief Justice announces the votes for each candidate. The Hansard of each sitting, on parliament.gov.za,
records the nominations and the announcement. The 1994 election is not in the Assembly's published Hansard.
"""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "za.na_president"
SOUTH_AFRICA = str(uuid5(package.NAMESPACE, "polity\x1fZA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fZAF"))
SCHEME = "za.nation"
HOME = "https://www.parliament.gov.za/hansard"
MEDIA = "https://www.parliament.gov.za/storage/app/media/Docs/"
VOTES = 1
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


PARLIAMENT = key("source", "parliament")
PRESIDENCY = key("office", "president")
ASSEMBLY = key("body", "national-assembly")
SYSTEM = key("electoral_system", "president")


@dataclass(frozen=True)
class Candidate:
    name: str  # as the Chief Justice's announcement prints it
    full: str  # as the nomination prints it
    votes: int | None
    needle: str  # a phrase of the Hansard that proves the vote or nomination, matched on whitespace-folded text


@dataclass(frozen=True)
class Election:
    day: date
    file: str
    candidates: tuple[Candidate, ...]
    winner: str
    note: str


ELECTIONS = (
    Election(
        date(1999, 6, 14),
        "hansard/92082_1.doc",
        (
            Candidate(
                "Thabo Mvuyelwa Mbeki",
                "Thabo Mvuyelwa Mbeki",
                None,
                "nominated Mr Thabo Mvuyelwa Mbeki for election as President of the Republic of South Africa",
            ),
        ),
        "Thabo Mvuyelwa Mbeki",
        "The Hansard records one nomination, by Dr Nkosazana Clarice Zuma and seconded by Mr Mosiuoa Gerhard Patrick "
        "Lekota, and the President of the Constitutional Court's declaration that the nominee was elected.",
    ),
    Election(
        date(2004, 4, 23),
        "hansard/106725_1.doc",
        (
            Candidate(
                "Thabo Mvuyelwa Mbeki",
                "Thabo Mvuyelwa Mbeki",
                None,
                "nominated Mr Thabo Mvuyelwa Mbeki for election as President of the Republic of South Africa",
            ),
        ),
        "Thabo Mvuyelwa Mbeki",
        "The Hansard records one nomination, by Ms Nosiviwe Noluthando Mapisa-Nqakula and seconded by Mr Mosiuoa "
        "Gerard Patrick Lekota, and the Chief Justice's declaration that the nominee was elected.",
    ),
    Election(
        date(2008, 9, 25),
        "hansard/176241_1.doc",
        (
            Candidate("Kgalema Petrus Motlanthe", "Kgalema Petrus Motlanthe", 269, "Mr K P Motlanthe: 269"),
            Candidate("Wetshotsile Joseph Seremane", "Wetshotsile Joseph Seremane", 50, "Mr W J Seremane: 50"),
        ),
        "Kgalema Petrus Motlanthe",
        "The Hansard records two nominations, a secret ballot, and the Chief Justice's announcement of the votes for "
        "each candidate. It does not print the number of ballots cast or spoilt, so only the candidates' votes are "
        "stored. Mr Seremane was nominated by the Leader of the Opposition.",
    ),
    Election(
        date(2009, 5, 6),
        "hansard/254492_1.doc",
        (
            Candidate(
                "Jacob Gedleyihlekisa Zuma", "Jacob Gedleyihlekisa Zuma", 277, "Mr Jacob Gedleyihlekisa Zuma: 277"
            ),
            Candidate(
                "Hamilton Mvumelwano Dandala", "Hamilton Mvumelwano Dandala", 47, "Mr Hamilton Mvumelwano Dandala: 47"
            ),
        ),
        "Jacob Gedleyihlekisa Zuma",
        "The Hansard records two nominations, a secret ballot, and the Chief Justice's announcement of the votes for "
        "each candidate. It does not print the number of ballots cast or spoilt, so only the candidates' votes are "
        "stored. The Democratic Alliance announced that it would abstain from the ballot. Mr Dandala was nominated by "
        "the Congress of the People.",
    ),
    Election(
        date(2014, 5, 21),
        "hansard/584411_1.doc",
        (
            Candidate(
                "Jacob Gedleyihlekisa Zuma",
                "Jacob Gedleyihlekisa Zuma",
                None,
                "the Chief Justice declared Mr Jacob Gedleyihlekisa Zuma duly elected as President of the Republic",
            ),
        ),
        "Jacob Gedleyihlekisa Zuma",
        "The Hansard records one nomination, seconded by Mr Nkosiyakhe Amos Masondo, after the Chief Justice dismissed "
        "an objection to the nominee's eligibility, and the Chief Justice's declaration that the nominee was elected.",
    ),
    Election(
        date(2018, 2, 15),
        "hansard/941032ad-c456-4e28-aca5-f93a6304eef5.pdf",
        (
            Candidate(
                "Matamela Cyril Ramaphosa",
                "Matamela Cyril Ramaphosa",
                None,
                "Mr M C Ramaphosa accordingly elected President of the Republic",
            ),
        ),
        "Matamela Cyril Ramaphosa",
        "The Hansard records one nomination, by Dr Patrick Maesela for the African National Congress, and that there "
        "were no further nominations. The election followed the resignation of the previous President.",
    ),
    Election(
        date(2019, 5, 22),
        "hansard/18b990ef-6df0-407a-bb17-3ddcde7b1c3e.pdf",
        (
            Candidate(
                "Matamela Cyril Ramaphosa",
                "Matamela Cyril Ramaphosa",
                None,
                "the hon Cyril Matamela Ramaphosa is duly elected President of the Republic of South Africa",
            ),
        ),
        "Matamela Cyril Ramaphosa",
        "The Hansard records one nomination and the Chief Justice's declaration, under item 5 of Part A of Schedule 3, "
        "that the nominee was elected.",
    ),
    Election(
        date(2024, 6, 14),
        "hansard/2db8890e-33f8-490f-a0bc-b135bc2f1b07.pdf",
        (
            Candidate("Matamela Cyril Ramaphosa", "Matamela Cyril Ramaphosa", 283, "Mr M C Ramaphosa — 283"),
            Candidate("Julius Sello Malema", "Julius Sello Malema", 44, "Mr J S Malema — 44"),
        ),
        "Matamela Cyril Ramaphosa",
        "The Hansard records two nominations, Mr Ramaphosa's by Mr Mdumiseni Ntuli and seconded by Mr Velenkosini "
        "Hlabisa, Mr Malema's by Mr Nqabayomzi Lawrence Saziso Kwankwa, a secret ballot, and the Chief Justice's "
        "announcement of the votes for each candidate. It does not print the number of ballots cast or spoilt, so "
        "only the candidates' votes are stored. This Hansard is marked unrevised.",
    ),
)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def url(election: Election) -> str:
    return MEDIA + election.file


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, url(election), suffix=Path(election.file).suffix)


def text(path: Path) -> str:
    if path.suffix == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
        return " ".join(out.stdout.split())
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / "in.doc"
        shutil.copy(path, copy)
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", work, str(copy)],
            check=True,
            capture_output=True,
        )
        return " ".join((Path(work) / "in.txt").read_text(errors="replace").replace("\xa0", " ").split())


def check(election: Election) -> None:
    body = text(raw.cached(SOURCE, url(election)).path)
    for candidate in election.candidates:
        if re.sub(r"\s+", " ", candidate.needle) not in body:
            raise ValueError(f"{election.day}: the Hansard does not print {candidate.needle!r}")


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
        "The presidency of South Africa, the National Assembly, and the electoral system of the Assembly's election "
        "of the President",
    )
    p.external |= {SOUTH_AFRICA, COUNTRY}
    p.add("scheme", code=SCHEME, polity_id=SOUTH_AFRICA, kind="administrative", rank=1, noun="nation", code_system=None)
    p.add("office", id=PRESIDENCY, polity_id=SOUTH_AFRICA, kind="head_of_state")
    p.name(PRESIDENCY, "President of South Africa", "en", "official")
    p.add("body", id=ASSEMBLY, polity_id=SOUTH_AFRICA, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=SOUTH_AFRICA, rules={"formula": "fptp"}, rules_version=1)
    p.name(SYSTEM, "Election of the President by the National Assembly", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The National Assembly elects the President from among its members at its first sitting after a "
        "general election and whenever the office falls vacant (Constitution, section 86). The Chief Justice presides. "
        "When one candidate is nominated, the Chief Justice declares that candidate elected and no vote is taken. When "
        "more are nominated, the members vote by secret ballot and the Chief Justice announces the votes for each "
        "candidate.",
        lang="en",
    )
    p.add(
        "source",
        id=PARLIAMENT,
        kind="official_body",
        publisher="Parliament of the Republic of South Africa",
        title="Hansard of the National Assembly",
        url=HOME,
        license="No license stated; published by Parliament for public use",
        attribution="Parliament of the Republic of South Africa",
    )
    p.add(
        "reporting_unit",
        id=key("unit", "national"),
        polity_id=SOUTH_AFRICA,
        scheme_code=SCHEME,
        code="ZA",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    for full in dict.fromkeys(c.full for e in ELECTIONS for c in e.candidates):
        person = key("person", full)
        p.add("person", id=person)
        p.name(person, full, "en", "official")
    return p


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The National Assembly's election of the President on {day:%-d %B %Y}, from the Assembly's Hansard",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {SOUTH_AFRICA, COUNTRY}
    p.artifact(raw.cached(SOURCE, url(election)), PARLIAMENT)
    contested = len(election.candidates) > 1
    id = key("election", day)
    path = f"za/{day}-president"
    p.add("election", id=id, polity_id=SOUTH_AFRICA, kind="indirect", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day.year} South African presidential election", "en", "official")
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=PRESIDENCY,
        body_id=ASSEMBLY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted" if contested else "tacit",
    )
    p.name(contest, f"{day.year} presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    kind = "counted" if contested else "tacit"
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind=kind,
        source_id=PARLIAMENT,
        status="active",
        is_default=True,
        completeness=1.0 if contested else 0.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=election.note,
        lang="en",
    )
    unit = key("unit", "national")
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    for position, candidate in enumerate(election.candidates, 1):
        option = key("option", day, candidate.full)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=candidate.name,
        )
        person = key("person", candidate.full)
        p.add(
            "candidacy",
            id=key("candidacy", day, candidate.full),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
            is_independent=False,
        )
        if candidate.full == election.winner:
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=1)
        if candidate.votes is not None:
            rows.append((result_set, unit, option, VOTES, candidate.votes, 0))
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
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
