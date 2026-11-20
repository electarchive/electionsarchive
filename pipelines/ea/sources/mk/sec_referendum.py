"""North Macedonia: the national referendum of 30 September 2018 from the State Election Commission.

The Commission's report on the result of the vote, adopted on 1 October 2018 (no. 12-2139/1), gives the national
totals: the registered voters, the voters, the invalid and valid ballots, and the votes for and against. Its report
on the conduct of the referendum, adopted later in October 2018, splits the same totals between the polling boards
in the country and those in the diplomatic and consular missions abroad; the two halves are checked against the
decision here. Both are published as text PDFs, in the Commission's Albanian edition, on its referendum page.

The results by municipality and polling station sit on the Commission's results service, which no route the
archive can use reaches, and the referendum of 7 November 2004 is not in any file within reach either.
"""

import re
import subprocess
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "mk.sec_referendum"
MACEDONIA = str(uuid5(package.NAMESPACE, "polity\x1fMK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMKD"))
# The national reporting unit of mk.sobranie_president (scheme mk.nation, code MK), which is not tied to one day.
NATION_UNIT = "ad647fbd-0364-58c0-b57c-a1478f3567c4"
DAY = date(2018, 9, 30)
KEY = f"{SOURCE}/{DAY}"
PAGE = "https://www.sec.mk/referendumi-30-09-2018/?lang=sq"
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download"
DECISION = DRIVE.format("1KmHAbjkTLTAY7mXlHSripB-VnbYluK2x")
REPORT = DRIVE.format("1UuRuz6AAypcPc0VPLNSjkRu1iy6qJT42")
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14

# The figures of the decision of 1 October 2018, in the order it prints them.
LABELS = {
    "registered": "Numri total i qytetarëve të regjistruar në Listën zgjedhore",
    "voters": "Numri total i qytetarëve të cilët kanë votuar",
    "unused": "Numri total i fletëvotimeve të papërdorura",
    "invalid": "Numri total i fletëvotimeve të pavlefshme",
    "valid": "Numri total i fletëvotimeve të vlefshme",
    "for": "Numri total i votave ,,PRO’’",
    "against": "Numri total i votave ,,KUNDËR’’",
}
# The halves of the conduct report: in the country, and in the 33 missions abroad.
HOME = {"registered": 1_803_642, "voters": 665_476, "for": 608_629, "against": 37_640, "invalid": 19_198}
ABROAD = {"registered": 2_694, "voters": 1_267, "for": 1_184, "against": 60, "invalid": 23}

METHOD = (
    "National totals of the State Election Commission's report on the result of the vote of the national referendum "
    "of 30 September 2018, adopted on 1 October 2018 (no. 12-2139/1) and read from the text of its Albanian edition. "
    "Its report on the conduct of the referendum gives the same totals in two parts, the polling boards in the "
    "country and the 33 in diplomatic and consular missions abroad; the parts add up to the decision's totals. The "
    "decision counts 9 more voters (666,743) than valid and invalid ballots together (666,734), as the polling "
    "boards' minutes do in the country. The question did not pass: the law on referendums adopts a decision only "
    "when more than half of the registered voters vote and a majority of them vote for it, and 36.91 percent voted."
)
CONTEXT = (
    "A consultative referendum called by the Speaker of the Assembly (decision 08-4666/1 of 30 July 2018) on the "
    "question: Are you for membership in the EU and NATO by accepting the Agreement between the Republic of "
    "Macedonia and the Republic of Greece? A decision needed more than half of the 1,806,336 registered voters, "
    "903,169, to vote, and a majority of the voters to vote for it."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [KEY]


def fetch() -> None:
    for url in (DECISION, REPORT):
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def _text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def decision() -> dict[str, int]:
    """The decision's national figures, read from its text and checked against each other and the report."""
    text = " ".join(_text(DECISION).split())
    out: dict[str, int] = {}
    for name, label in LABELS.items():
        found = re.search(re.escape(label) + r"\s+([\d.]+)", text)
        if found is None:
            raise ValueError(f"{KEY}: the decision prints no {name!r}")
        out[name] = int(found.group(1).replace(".", ""))
    if out["for"] + out["against"] != out["valid"]:
        raise ValueError(f"{KEY}: for and against do not add up to the valid ballots: {out}")
    if out["voters"] + out["unused"] != out["registered"]:
        raise ValueError(f"{KEY}: voters and unused ballots do not add up to the registered voters: {out}")
    for name, value in HOME.items():
        if value + ABROAD[name] != out[name]:
            raise ValueError(f"{KEY}: the report's {name} do not add up to the decision's {out[name]}")
    report = " ".join(_text(REPORT).split())
    for figure in (*HOME.values(), *ABROAD.values()):
        if f"{figure:,}".replace(",", ".") not in report:
            raise ValueError(f"{KEY}: the conduct report does not print {figure}")
    if 2 * out["voters"] > out["registered"]:
        raise ValueError(f"{KEY}: turnout reached half the register, but the decision says the question failed")
    return out


def build() -> Package:
    totals = decision()
    p = Package(
        KEY,
        SOURCE,
        "North Macedonia's national referendum of 30 September 2018, national totals from the State Election "
        "Commission's report on the result",
    )
    p.external |= {MACEDONIA, COUNTRY, NATION_UNIT}
    sec = key("source", "sec", DAY)
    p.add(
        "source",
        id=sec,
        kind="official_body",
        publisher="Државна изборна комисија",
        title="Referendumi 30.09.2018: Raport për rezultatin e votimit dhe Raport i zbatimit të referendumit",
        url=PAGE,
        license="No license stated; published by the State Election Commission for public use",
        attribution="Државна изборна комисија на Република Македонија",
        notes="The report on the result of the vote of 1 October 2018 (no. 12-2139/1) and the report on the conduct "
        "of the referendum, in the Albanian edition linked from the Commission's referendum page.",
    )
    for url in (DECISION, REPORT):
        p.artifact(raw.cached(SOURCE, url), sec)

    election = p.id("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=MACEDONIA,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "2018 Macedonian referendum on the Prespa agreement", "en", "official")
    p.name(election, "Референдум на државно ниво 2018", "mk", "native", False)
    p.name(election, "Referendumi në nivel shtetëror 2018", "sq", "native", False)
    base = f"mk/{DAY}-referendum"
    p.add("slug", path=base, entity_id=election, is_current=True)

    system = key("electoral_system", "referendum")
    p.add(
        "electoral_system",
        id=system,
        polity_id=MACEDONIA,
        rules={"formula": "referendum", "quorum": 0.5, "minimum": {"share": 0.5, "strict": True, "of": "ballots"}},
        rules_version=1,
    )
    contest = p.id("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=COUNTRY,
        electoral_system_id=system,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(
        contest,
        "Are you for membership in the EU and NATO by accepting the Agreement between the Republic of Macedonia and "
        "the Republic of Greece?",
        "en",
        "official",
    )
    p.name(
        contest,
        "Дали сте за членство во ЕУ и НАТО со прифаќање на Договорот меѓу Република Македонија и Република Грција?",
        "mk",
        "native",
        False,
    )
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    p.add("note", id=p.id("note", contest, "context"), entity_id=contest, kind="context", body_md=CONTEXT, lang="en")
    options = {}
    for position, (answer, english, macedonian) in enumerate(
        (("for", "For", "ЗА"), ("against", "Against", "ПРОТИВ")), 1
    ):
        option = options[answer] = p.id("option", DAY, answer)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=macedonian,
        )
        p.name(option, english, "en", "official")
        p.name(option, macedonian, "mk", "native", False)

    result_set = p.id("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=sec,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    # Turnout fell short of half the register, so the question failed whatever the answers.
    p.add("outcome", result_set_id=result_set, option_id=options["for"], kind="lost")

    rows = [
        (options["for"], VOTES, totals["for"]),
        (options["against"], VOTES, totals["against"]),
        (None, REGISTERED, totals["registered"]),
        (None, VOTERS, totals["voters"]),
        (None, VALID, totals["valid"]),
        (None, INVALID, totals["invalid"]),
    ]
    p.frame(
        "result_value",
        pl.DataFrame(
            rows, schema={"option_id": pl.String, "measure": pl.Int16, "value": pl.Int64}, orient="row"
        ).with_columns(
            result_set_id=pl.lit(result_set),
            unit_id=pl.lit(NATION_UNIT),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        ),
    )
    print(
        f"{KEY}: for {totals['for']:,}, against {totals['against']:,}, voters {totals['voters']:,} of "
        f"{totals['registered']:,}; the decision's totals and the report's halves agree",
        flush=True,
    )
    return p
