"""Alaska's November 3, 2026 Superior Court and District Court retention elections, by precinct.

The 2026 general election and its Supreme Court retention contest already exist. This pipeline mints the 20 trial-court
retention contests on the same ballot (a judge each, voted Yes or No within the judge's judicial district), reusing the
existing Alaska trial-court offices, and attaches the Division of Elections' unofficial first-choice results at the
grain of the precinct and House-district aggregate units the precinct pipeline minted.
"""

import csv
import re
from collections import defaultdict
from uuid import uuid5

import duckdb
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.us import ak_general_2026 as general
from ea.sources.us.ak_general_2026 import DAY, MEASURES, number, place_of, summary
from ea.sources.us.retention import display

SOURCE = "us.ak_judicial_2026"
KEY = f"{SOURCE}/general"
PUBLISHER = general.PUBLISHER
TITLE = "2026 General Election results by precinct: judicial retention"
OFFICES = {  # the archive's existing trial-court offices
    "Superior": ("36b15f66-ae86-59c2-bdf2-b532d61feee3", "Superior Court"),
    "District": ("470678f7-339c-57d9-a60c-0af512d9505e", "District Court"),
}
DISTRICTS = {"1": "First", "2": "Second", "3": "Third", "4": "Fourth"}
TITLE_RE = re.compile(r"(Superior|District) Court JD(\d) - (.+)")
METHOD = (
    "A retention election asks whether a sitting judge should stay in office, with Yes and No as the only answers. "
    "Alaska's trial judges stand for retention within their judicial district only, so the contest is recorded "
    "against the state. Votes by precinct are from the Division of Elections' results file, which the Division marks "
    "unofficial until the State Review Board certifies the election; the file prints the judge's surname only. "
    "Precincts report the ballots cast on election day; the absentee, early, and questioned ballots are reported by "
    "House district as their own units, and the federal overseas absentee ballots statewide. Ballots cast, "
    "overvoted ballots (as invalid), and undervoted ballots (as blank) are the file's counts for the contest, and "
    "registered voters are the precinct's."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    raw.fetch(SOURCE, general.PRECINCTS, suffix=".csv")
    raw.fetch(SOURCE, general.SUMMARY, suffix=".pdf")


def build() -> list[Package]:
    artifact = raw.cached(SOURCE, general.PRECINCTS)
    summary_artifact = raw.cached(SOURCE, general.SUMMARY)
    totals = summary()
    con = duckdb.connect()
    ref = str(general.REFERENCE)
    polity = con.execute(f"select id from '{ref}/polity.parquet' where iso_code = 'US-AK'").fetchone()
    election = con.execute(
        f"select id from '{ref}/election.parquet' where polity_id = ? and date_from = date '{DAY}' and name = ?",
        [polity[0] if polity else None, general.ELECTION],
    ).fetchall()
    state = con.execute(
        f"select id, area_id from '{ref}/reporting_unit.parquet' where polity_id = ? and scheme_code = 'us.state'",
        [polity[0] if polity else None],
    ).fetchone()
    if polity is None or len(election) != 1 or state is None:
        raise LookupError("the reference lacks the Alaska polity, election, or state unit")
    held_units = {
        code: str(uid)
        for uid, code in con.execute(
            f"select id, code from '{ref}/reporting_unit.parquet' where code like '2026:AK:%' or code like 'AK:2026:%'"
        ).fetchall()
    }
    held_contests = {
        title
        for (title,) in con.execute(
            f"select name from '{ref}/contest.parquet' where election_id = ? and name like '%Judicial District, retention%'",
            [election[0][0]],
        ).fetchall()
    }
    if held_contests:
        raise ValueError(f"the archive already holds {len(held_contests)} trial-court retention contests")

    places = {}
    registered: dict[str, int] = {}
    votes: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    counts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    with artifact.path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            title = row["Contest_title"]
            if not TITLE_RE.fullmatch(title):
                continue
            place = places.setdefault(row["Precinct_name"].strip(), place_of(row["Precinct_name"]))
            if place.code not in held_units:
                raise LookupError(f"{place.code}: the archive holds no such unit")
            if row["Reg_voters"] and place.kind == "precinct":
                registered[place.code] = number(row["Reg_voters"])
            column = place.column
            if row["total_ballots"] != row[f"{column}_ballots"] or row["total_votes"] != row[f"{column}_votes"]:
                raise ValueError(f"{place.code} {title}: the total isn't the {column} column")
            votes[title][place.code][row["candidate_name"].strip()] = number(row["total_votes"])
            counts[title][place.code] = {
                "ballots": number(row["total_ballots"]),
                "blank": number(row["total_under_votes"] or "0"),
                "invalid": number(row["total_over_votes"] or "0"),
            }
    by_code = {place.code: place for place in places.values()}

    p = Package(
        KEY,
        SOURCE,
        "Alaska's November 3, 2026 Superior Court and District Court retention contests, with the unofficial "
        "results by precinct",
    )
    src = key("source", "results")
    p.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=general.SITE,
        license="Public record",
        attribution=PUBLISHER,
    )
    p.artifact(artifact, src)
    p.artifact(summary_artifact, src)
    p.external |= {polity[0], election[0][0], state[0], state[1]}
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    persons: set[str] = set()
    for title in sorted(votes):
        found = TITLE_RE.fullmatch(title)
        if found is None:
            raise ValueError(title)
        court, district, judge = found.groups()
        office, court_name = OFFICES[court]
        shown = display(judge)
        stem = f"{court_name}, {DISTRICTS[district]} Judicial District"
        contest_key = f"{DAY}:AK:{court.lower()}-jd{district}-retention-{shown.lower()}"
        contest = p.id("contest", contest_key)
        p.external.add(office)
        p.add(
            "contest",
            id=contest,
            election_id=election[0][0],
            kind="office",
            office_id=office,
            district_area_id=state[1],
            term="full",
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{DAY.year} Alaska {stem}, retention: {shown}", "en", "official")
        person = p.id("person", f"AK:{court}{district}:{shown}")
        if person not in persons:
            persons.add(person)
            p.add("person", id=person)
            p.name(person, shown, "en", "official")
        options = {}
        for position, answer in enumerate(("Yes", "No"), start=1):
            options[answer] = p.id("option", contest_key, answer)
            p.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=answer,
            )
        p.add(
            "candidacy",
            id=p.id("candidacy", contest_key),
            person_id=person,
            option_id=options["Yes"],
            role="head",
            ballot_name=shown,
            is_independent=False,
        )
        result_set = p.id("result_set", contest_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election[0][0],
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=src,
            status="active",
            is_default=True,
            completeness=1.0,
            as_of=artifact.retrieved_at,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        by_answer: dict[str, int] = defaultdict(int)
        for code, by_name in votes[title].items():
            unit = held_units[code]
            p.external.add(unit)
            for answer, amount in by_name.items():
                rows.append((result_set, unit, options[answer.title()], by_code[code].channel, 1, amount))
                by_answer[answer.title()] += amount
            for measure, amount in counts[title][code].items():
                rows.append((result_set, unit, None, by_code[code].channel, MEASURES[measure], amount))
            if code in registered:
                rows.append((result_set, unit, None, 0, MEASURES["registered"], registered[code]))
        for answer, total in by_answer.items():
            published = totals[title]
            declared = published.get(answer.upper())
            if declared is None or declared != total:
                raise ValueError(f"{title}: {answer} sums to {total}, the summary says {declared}")
            p.add("declared", result_set_id=result_set, option_id=options[answer], measure=1, value=total)
    p.frame(
        "result_value",
        pl.DataFrame(
            [(*row[:5], 0, 0, row[5]) for row in rows],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ),
    )
    print("contests:", len(votes), "values:", len(rows))
    return [p]


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
