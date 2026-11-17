"""Cyprus: the Greek Cypriot community's referendum of 24 April 2004 on the Annan plan, by ballot box.

The Ministry of Interior publishes the official result on the national open data portal as one file with the Yes
and No votes of each ballot box, under its district, municipality or community, and polling centre. The results site
that `moi.py` crawls does not hold the referendum. Ballot boxes are placed in the communities of `cy.moi/geo`: the
Ministry's June 2004 European election lists the same 334 municipalities and communities under the same names, and
its district pages give each one's id on the results site, which is the id `cy.moi/geo` keys its areas by.
"""

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.cy import crawl
from ea.sources.cy.moi import COUNTRY, CYPRUS, MOI_SOURCE, SOURCE, VOTES, area, key

DAY = date(2004, 4, 24)
KEY = f"{SOURCE}/{DAY}"
DEPENDS = [f"{SOURCE}/geo"]
DATASET = "https://data.gov.cy/el/dataset/episima-apotelesmata-dimopsifismatos-24is-aprilioy-2004"
CSV = "https://data.gov.cy/sites/default/files/el_results_24042004.csv"
LAW = "https://www.cylaw.org/nomoi/arith/2004_1_074.pdf"
EURO_2004 = 41
DISTRICTS = {"ΛΕΥΚΩΣΙΑ": 1, "ΑΜΜΟΧΩΣΤΟΣ": 3, "ΛΑΡΝΑΚΑ": 4, "ΛΕΜΕΣΟΣ": 5, "ΠΑΦΟΣ": 6}
DATA_SOURCE = key("source", "data.gov.cy", DAY)
LAW_SOURCE = key("source", "law", "74(I)/2004")
YES, NO = "ΝΑΙ", "ΟΧΙ"
# The island totals of the Yes and No votes the Ministry announced; the file prints none, so they are only compared.
ANNOUNCED = {YES: 99_976, NO: 313_704}

METHOD = (
    "Official result published by the Ministry of Interior on the national open data portal, with the Yes and No "
    "votes of each ballot box. Each box is stored as its own reporting unit, named by its polling centre and centre "
    "letter, and placed in its municipality or community. The file gives no registered voters, ballots cast, or "
    "invalid and blank ballots, so only the Yes and No votes are stored."
)
CONTEXT = (
    "On 24 April 2004 the Greek Cypriot and Turkish Cypriot communities voted in separate referendums on whether they "
    "accepted, as it stood, the final plan of the Secretary-General of the United Nations of 31 March 2004 as a "
    "settlement of the Cyprus problem. Law 74(I)/2004 provided for the Greek Cypriot referendum, organized by the "
    "Republic's election officials. Only this referendum is stored here. The plan, which needed a Yes in both "
    "referendums, did not come into force."
)


@dataclass(frozen=True)
class Box:
    district: str
    town: str
    number: str
    centre: str
    letter: str
    yes: int
    no: int


def district_url(district: int) -> str:
    return crawl.API.format(lang="Greek", election=EURO_2004, district=district, area="", station="", box="")


def fetch() -> None:
    raw.fetch(SOURCE, CSV, suffix=".csv")
    raw.fetch(SOURCE, LAW, suffix=".pdf")
    for district in DISTRICTS.values():
        raw.fetch(SOURCE, district_url(district), suffix=".json")


def packages() -> list[str]:
    return [KEY]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    build().write()


def boxes() -> list[Box]:
    text = raw.cached(SOURCE, CSV).path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    expected = ["DISTRICT", "TOWN", "BALLOT_BOX", "ELECTION_CENTRE", "CENTRE", "YES/NO", "VOTES"]
    if not rows or list(rows[0]) != expected:
        raise ValueError(f"{KEY}: unexpected columns {list(rows[0]) if rows else []}")
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        place = (row["DISTRICT"].strip(), row["BALLOT_BOX"].strip())
        entry = found.setdefault(
            place,
            {
                "town": row["TOWN"].strip(),
                "centre": " ".join(row["ELECTION_CENTRE"].split()),
                "letter": " ".join(row["CENTRE"].split()),
                "votes": {},
            },
        )
        if (entry["town"], entry["centre"]) != (row["TOWN"].strip(), " ".join(row["ELECTION_CENTRE"].split())):
            raise ValueError(f"{KEY}: ballot box {place} is listed under two places")
        answer = row["YES/NO"].strip()
        if answer not in (YES, NO) or answer in entry["votes"]:
            raise ValueError(f"{KEY}: ballot box {place} has an unexpected or repeated answer {answer!r}")
        entry["votes"][answer] = int(row["VOTES"])
    out = []
    for (district, number), entry in found.items():
        if set(entry["votes"]) != {YES, NO}:
            raise ValueError(f"{KEY}: ballot box {district} {number} lacks an answer")
        out.append(
            Box(
                district,
                entry["town"],
                number,
                entry["centre"],
                entry["letter"],
                entry["votes"][YES],
                entry["votes"][NO],
            )
        )
    return out


def communities() -> dict[tuple[str, str], int]:
    """Each municipality and community of the June 2004 results, by district and Greek name, with its site id."""
    out: dict[tuple[str, str], int] = {}
    for name, district in DISTRICTS.items():
        document = json.loads(raw.cached(SOURCE, district_url(district)).path.read_text())
        assert isinstance(document, dict)
        if document["CurrentResult"]["Node"]["Id"] != district:
            raise ValueError(f"{KEY}: district page {district} is for another district")
        for row in document["Areas"]:
            out[(name, " ".join(row["Name"].split()))] = int(row["Id"])
    return out


def build() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The 24 April 2004 Greek Cypriot referendum on the Annan plan by ballot box from the Ministry of Interior",
        depends=DEPENDS,
    )
    p.external |= {CYPRUS, COUNTRY}
    p.add(
        "source",
        id=DATA_SOURCE,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών",
        title="Επίσημα Αποτελέσματα Δημοψηφίσματος 24ης Απριλίου 2004",
        url=DATASET,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Republic of Cyprus, Ministry of Interior, via the National Open Data Portal",
    )
    p.add(
        "source",
        id=LAW_SOURCE,
        kind="official_body",
        publisher="Βουλή των Αντιπροσώπων",
        title="Ο περί Δημοψηφίσματος (24 Απριλίου 2004) Νόμος, 74(I)/2004",
        url=LAW,
        license="Statute of the Republic of Cyprus; official text published in the Official Gazette",
        license_url=LAW,
        attribution="Republic of Cyprus, Official Gazette, Annex I(I), No. 3835, 14 April 2004",
    )
    p.artifact(raw.cached(SOURCE, CSV), DATA_SOURCE)
    p.artifact(raw.cached(SOURCE, LAW), LAW_SOURCE)
    for district in DISTRICTS.values():
        p.artifact(raw.cached(SOURCE, district_url(district)), MOI_SOURCE)

    election = key("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=CYPRUS,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(election, "2004 Greek Cypriot referendum on the Annan plan", "en", "official")
    p.name(election, "Δημοψήφισμα της 24ης Απριλίου 2004", "el", "native", False)
    path = f"cy/{DAY}-referendum"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, "2004 Greek Cypriot referendum: do you accept the Annan plan?", "en", "official")
    p.name(
        contest,
        "Δημοψήφισμα για το Σχέδιο του Γενικού Γραμματέα των Ηνωμένων Εθνών της 31ης Μαρτίου 2004",
        "el",
        "native",
        False,
    )
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    p.add("note", id=key("note", contest, "context"), entity_id=contest, kind="context", body_md=CONTEXT, lang="en")
    p.add("citation", id=key("citation", contest, "law"), entity_id=contest, source_id=LAW_SOURCE)
    options = {}
    for position, (answer, english) in enumerate(((YES, "Yes"), (NO, "No")), 1):
        option = options[answer] = key("option", DAY, english.lower())
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=answer,
        )
        p.name(option, english, "en", "official")
        p.name(option, answer, "el", "native", False)

    result_set = key("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DATA_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")

    places = communities()
    rows: list[tuple[str, str, int]] = []
    totals = {YES: 0, NO: 0}
    for box in boxes():
        if box.district not in DISTRICTS:
            raise ValueError(f"{KEY}: unknown district {box.district}")
        community = places.get((box.district, box.town))
        if community is None:
            raise ValueError(f"{KEY}: {box.town} ({box.district}) is not among the June 2004 communities")
        unit = key("unit", DAY, DISTRICTS[box.district], box.number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CYPRUS,
            scheme_code="cy.ballot_box",
            code=f"{DAY}:{DISTRICTS[box.district]}:{box.number}",
            area_relation="within",
            area_id=area("cy.community", community),
            kind="standard",
        )
        p.name(unit, f"{box.centre} - {box.letter}", "el", "official")
        for answer, count in ((YES, box.yes), (NO, box.no)):
            rows.append((unit, options[answer], count))
            totals[answer] += count
    if totals != ANNOUNCED:
        raise ValueError(f"{KEY}: ballot boxes sum to {totals}, the Ministry announced {ANNOUNCED}")
    print(f"{KEY}: {len(rows) // 2} ballot boxes, Yes {totals[YES]:,} and No {totals[NO]:,} match", flush=True)
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema={"unit_id": pl.String, "option_id": pl.String, "value": pl.Int64}, orient="row")
        .with_columns(
            result_set_id=pl.lit(result_set),
            measure=pl.lit(VOTES, pl.Int16),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        )
        .select("result_set_id", "unit_id", "option_id", "measure", "value", "channel", "count_round", "flags"),
    )
    p.add("outcome", result_set_id=result_set, option_id=options[NO], kind="won")
    p.add("outcome", result_set_id=result_set, option_id=options[YES], kind="lost")
    return p
