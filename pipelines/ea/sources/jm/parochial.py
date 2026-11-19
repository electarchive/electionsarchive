"""Jamaica's parochial board and parish council general elections of 1951 to 1990, by electoral division.

The report of each of these elections is a scan without a text layer. The Chief Electoral Officer's reports of 1951 to
1977 print, in Part IV, "Name, occupation, etc. of candidates by electoral division", and the Director of Elections'
reports of 1981 and 1990 print, in Part I, "Total ballots cast for each candidate", one table per parish: each
division's candidates with their occupation, party and votes, the total votes cast in the division, the rejected
ballots, and the electors on the list. Those tables were read from the page images and are kept as one JSON file per
election in `scans/`, with the PDF page each division was read from. The tables print no polling-station figures, so
each division is one reporting unit. The 1981 and 1990 reports also print a summary of the poll by parish council,
which the division figures are checked against.
"""

import functools
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ea import changeset, raw
from ea.package import Package
from ea.sources.jm import ecj, local

SOURCE = ecj.SOURCE
JAMAICA = ecj.JAMAICA
VOTES, REGISTERED, CAST, VALID, INVALID = ecj.VOTES, ecj.REGISTERED, ecj.CAST, ecj.VALID, ecj.INVALID
HERE = Path(__file__).parent / "scans"
BROWSER = "browser://20261117-084823-667659"
BASE = "https://www.ecj.com.jm/wp-content/uploads"
WAYBACK = "https://web.archive.org/web"
KEY = "jm.ecj/local-registry-divisions"
SCHEME = "jm.division"
GENERAL_TAGS = frozenset(("FP", "NLP", "PFM"))  # parties the parliamentary registry package already holds
# The report of each general election, by election day. A browser:// file is one saved from the authority's site by
# hand, whose page the saved copy does not name.
FILES: dict[str, str] = {
    "1951-06-28": f"{BROWSER}/1951-KSAC-Parochial-Boards-General-Elections.pdf",
    "1956-03-21": f"{BROWSER}/1956-KSAC-Parochial-General-Elections.pdf",
    "1960-03-01": f"{BROWSER}/1960-KSAC-Parish-Councils-General-Elections.pdf",
    "1969-03-18": f"{BROWSER}/1969-KSAC-Parish-Councils-General-Elections.pdf",
    "1974-02-05": f"{BROWSER}/1974-KSAC-Parish-Councils-General-Elections.pdf",
    "1977-03-08": f"{BROWSER}/1977-KSAC-Parish-Councils-General-Elections.pdf",
    "1981-03-02": f"{BASE}/2017/12/1981ParishCouncilElectionBoxbyBox.pdf",
    "1990-03-06": f"{WAYBACK}/20240623004105id_/https://ecj.com.jm/wp-content/uploads/2017/10/"
    "1990parishcouncilelectionresult_Part1.pdf",
}
# The authority's summary of the poll of each election that has one apart from its report, by election day.
SUMMARIES: dict[str, str] = {
    "1981-03-02": f"{WAYBACK}/20240623005829id_/https://ecj.com.jm/wp-content/uploads/2017/12/"
    "1981ParishCouncilElectionSummary.pdf",
    "1990-03-06": f"{WAYBACK}/20240622121350id_/https://ecj.com.jm/wp-content/uploads/2017/10/"
    "1990ParishCouncilElectionSummary.pdf",
}
# The table each report's division figures were read from, by election day; the others are the Part IV of the Chief
# Electoral Officer's report.
TABLES: dict[str, str] = {
    "1981-03-02": "Table V of Part I of the Director of Elections' printed report, \"Total ballots cast for each "
    'candidate"',
    "1990-03-06": "Table IV of Part I of the Director of Elections' printed report, \"Total ballots cast for each "
    'candidate"',
}
PART_IV = (
    "Part IV of the Chief Electoral Officer's printed report, \"Name, occupation, etc. of candidates by electoral "
    'division"'
)
DAYS = tuple(sorted(FILES))
# The parties the reports print that no other registry package holds, with their names where the report gives them.
PARTY_NAMES: dict[str, str | None] = {
    "MRA": None,
    "PNU": None,
    "NA": None,
    "UPO": None,
    "WPJ": "Workers Party of Jamaica",
    "ULP": None,
    "JFP": None,
}
METHOD = (
    "Digitized from {table}, {where} of the scanned file. The table prints each candidate's votes, the total votes cast "
    "in the division including rejected ballots, the rejected ballots, and the number of names on the list of "
    "electors; valid votes are the total less the rejected ballots. The report prints no result by polling station in "
    "this table. Figures are stored as printed. The winner of a division is the candidate with the most votes."
)


@dataclass
class Candidate:
    name: str
    occupation: str | None
    party_printed: str
    party: str
    votes: int | None
    withdrew: bool = False


@dataclass
class Division:
    parish: str
    number: int
    name: str
    pages: list[int]
    unopposed: bool
    total: int | None
    rejected: int | None
    electors: int | None
    candidates: list[Candidate] = field(default_factory=list)
    note: str = ""
    # The ballot position of the candidate the report declares elected, where it differs from the candidate with the
    # most votes in the table.
    elected: int | None = None


@dataclass
class Report:
    day: str
    divisions: list[Division]
    left_out: list[str]
    # The summary of the poll by parish council, where the report has one: electors in contested divisions, ballots
    # cast, rejected ballots, and the votes of the PNP, the JLP, and every other candidate, by parish.
    summary: dict[str, list[int]] = field(default_factory=dict)
    # How the division figures differ from that summary, as `check` words it; the transcription records the expected
    # differences so that a new one fails the build.
    differences: list[str] = field(default_factory=list)


@functools.cache
def read(day: str) -> Report:
    body = json.loads((HERE / f"local-{day}.json").read_text(encoding="utf-8"))
    if body["day"] != day:
        raise ValueError(f"{day}: transcription is of {body['day']}")
    return Report(
        day,
        [
            Division(
                d["parish"],
                d["number"],
                d["name"],
                d["pages"],
                d["unopposed"],
                d["total"],
                d["rejected"],
                d["electors"],
                [
                    Candidate(c["name"], c["occupation"], c["party_printed"], c["party"], c["votes"], c["withdrew"])
                    for c in d["candidates"]
                ],
                d.get("note", ""),
                d.get("elected"),
            )
            for d in body["divisions"]
        ],
        body.get("left_out", []),
        body.get("summary", {}),
        body.get("differences", []),
    )


def packages() -> list[str]:
    return [KEY, *(f"jm.ecj/local-{day}" for day in DAYS)]


SUMMARY_COLUMNS = ("electors", "ballots cast", "rejected ballots", "PNP votes", "JLP votes", "other candidates' votes")


def differences(report: Report) -> list[str]:
    """How the division figures of each parish, added up, differ from the report's summary of the poll by council."""
    sums: dict[str, list[int]] = {}
    for d in report.divisions:
        if d.unopposed:
            continue
        row = sums.setdefault(d.parish, [0] * len(SUMMARY_COLUMNS))
        row[0] += d.electors or 0
        row[1] += d.total or 0
        row[2] += d.rejected or 0
        for c in d.candidates:
            row[3 if c.party == "PNP" else 4 if c.party == "JLP" else 5] += c.votes or 0
    out = []
    if set(sums) != set(report.summary):
        raise ValueError(f"{report.day}: parishes {sorted(sums)} against the summary's {sorted(report.summary)}")
    for parish, printed in report.summary.items():
        for column, shown, added in zip(SUMMARY_COLUMNS, printed, sums[parish], strict=True):
            if shown != added:
                out.append(f"{parish}: {column} {shown:,} in the summary and {added:,} in the division table")
    return out


def check(report: Report) -> None:
    found = differences(report)
    if found != report.differences:
        raise ValueError(f"{report.day}: the summary differs from the division table: {found}")


def store(day: str) -> raw.Artifact:
    return raw.cached(SOURCE, FILES[day])


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def registry() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The electoral division scheme of Jamaica's parish councils and the parties of the parochial board and parish "
        "council elections of 1951 to 1990 that no other registry holds, from the Chief Electoral Officer's and the "
        "Director of Elections' reports",
        depends=["jm.ecj/registry"],
    )
    p.external.add(JAMAICA)
    p.add("scheme", code=SCHEME, polity_id=JAMAICA, kind="electoral_district", rank=2, noun="electoral division")
    for tag, full in sorted(PARTY_NAMES.items()):
        id = ecj.party_id(tag)
        p.add("party", id=id, kind="party", polity_id=JAMAICA)
        if full:
            p.name(id, full, "en", "official")
            p.name(id, tag, "en", "abbreviation", False)
        else:
            p.name(id, tag, "en", "abbreviation")
    return p


def independent(tag: str) -> bool:
    return ecj.independent(tag)


def _valid(d: Division) -> int | None:
    if d.total is None:
        return None
    return d.total - (d.rejected or 0)


def build(day: str) -> Package:
    report = read(day)
    first = date.fromisoformat(day)
    tags = {c.party for d in report.divisions for c in d.candidates if not independent(c.party)}
    unknown = tags - {"PNP", "JLP"} - GENERAL_TAGS - set(PARTY_NAMES)
    if unknown:
        raise ValueError(f"{day}: parties {sorted(unknown)} are not in a registry package")
    depends = ["jm.ecj/registry", "jm.ecj/local-registry", KEY]
    if tags & GENERAL_TAGS:
        depends.append(ecj.GENERAL_REGISTRY)
    p = Package(
        f"jm.ecj/local-{day}",
        SOURCE,
        f"The Jamaican {'parochial board' if first.year < 1960 else 'parish council'} and Kingston and St. Andrew "
        f"Corporation general elections of {first} by electoral division, from the "
        f"{'Director of Elections' if day in TABLES else 'Chief Electoral Officer'}'s report",
        depends=depends,
    )
    p.external.add(JAMAICA)
    stored = store(day)
    p.artifact(stored, ecj.ECJ_SOURCE)
    if day in SUMMARIES:
        p.artifact(raw.cached(SOURCE, SUMMARIES[day]), ecj.ECJ_SOURCE)
        check(report)
    election = ecj.key("local-election", day)
    path = f"jm/{day}-local"
    p.add("election", id=election, polity_id=JAMAICA, kind="local", date_from=first, date_to=first, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{first.year} Jamaican local government elections", "en", "official")
    if report.differences:
        p.add(
            "note",
            id=ecj.key("note", election, "summary"),
            entity_id=election,
            kind="caveat",
            body_md="The division figures, added up by parish, differ from the report's summary of the poll by "
            "council: " + "; ".join(report.differences) + ". The division figures are stored as printed.",
            lang="en",
        )
    if report.left_out:
        p.add(
            "note",
            id=ecj.key("note", election, "left-out"),
            entity_id=election,
            kind="caveat",
            body_md=" ".join(report.left_out),
            lang="en",
        )
    names: dict[str, int] = {}
    for d in report.divisions:
        names[d.name] = names.get(d.name, 0) + 1
    flagged: dict[str, list[str]] = {}
    for d in report.divisions:
        parish = d.parish
        label = f"{d.name} division, {parish}"
        slug = changeset.slug(d.name) + (f"-{changeset.slug(parish)}" if names[d.name] > 1 else "")
        contest = ecj.key("local-contest", day, parish, d.number)
        tacit = d.unopposed
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=local.COUNCILS,
            magnitude=1,
            round_number=1,
            electoral_system_id=ecj.SYSTEM,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, label, "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = ecj.key("local-result-set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit" if tacit else "counted",
            source_id=ecj.ECJ_SOURCE,
            status="active",
            is_default=True,
            completeness=None if tacit else 1.0,
        )
        where = f"page {d.pages[0]}" if len(d.pages) == 1 else f"pages {', '.join(map(str, d.pages))}"
        p.add(
            "note",
            id=ecj.key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=(
                f"The report prints that the candidate was returned unopposed, so no poll was held ({where} of the "
                "scanned file)."
                if tacit
                else METHOD.format(table=TABLES.get(day, PART_IV), where=where)
            ),
            lang="en",
        )
        if d.note:
            p.add(
                "note",
                id=ecj.key("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=d.note,
                lang="en",
            )
        options: list[str] = []
        for position, candidate in enumerate(d.candidates, 1):
            person = ecj.key("local-person", contest, position)
            option = ecj.key("local-option", contest, position)
            options.append(option)
            p.add("person", id=person)
            p.name(person, candidate.name, "en", "official")
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
                id=ecj.key("local-candidacy", contest, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=None if independent(candidate.party) else ecj.party_id(candidate.party),
                is_independent=independent(candidate.party),
            )
        if tacit:
            if len(options) != 1:
                raise ValueError(f"{day} {label}: {len(options)} candidates returned unopposed")
            p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")
            continue
        unit = ecj.key("local-unit", day, parish, d.number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=JAMAICA,
            scheme_code=SCHEME,
            code=f"{day}:{changeset.slug(parish)}:{d.number}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"{d.name} division, {parish}", "en", "official")
        valid = _valid(d)
        measures = [(REGISTERED, d.electors), (CAST, d.total), (INVALID, d.rejected), (VALID, valid)]
        if d.total is not None and d.rejected is None:
            measures = [(REGISTERED, d.electors), (CAST, d.total), (VALID, valid)]
        for measure, value in measures:
            ecj._put(p, result_set, unit, None, measure, value)
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        votes = []
        running: list[str] = []
        for option, candidate in zip(options, d.candidates, strict=True):
            if candidate.withdrew:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="withdrew")
                continue
            running.append(option)
            votes.append(candidate.votes)
            ecj._put(p, result_set, unit, option, VOTES, candidate.votes)
            if candidate.votes is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
        if None not in votes and valid is not None and sum(v or 0 for v in votes) != valid:
            flagged.setdefault("accounting.votes_valid", []).append(label)
        if None not in (d.total, d.electors) and (d.total or 0) > (d.electors or 0):
            flagged.setdefault("accounting.turnout", []).append(label)
        if d.elected is not None:
            p.add("outcome", result_set_id=result_set, option_id=options[d.elected - 1], kind="won")
        elif votes and None not in votes:
            top = max(v or 0 for v in votes)
            winners = [o for o, v in zip(running, votes, strict=True) if v == top]
            if len(winners) == 1:
                p.add("outcome", result_set_id=result_set, option_id=winners[0], kind="won")
    reasons = {
        "accounting.votes_valid": "the candidates' votes do not add up to the total votes less the rejected ballots "
        "that the report prints (each division's caveat note gives the figures)",
        "accounting.turnout": "the report prints more votes cast than names on the list of electors",
    }
    for code, labels in flagged.items():
        p.accept(code, f"{day}: in {'; '.join(labels)}, {reasons[code]}; the figures are stored as printed")
    return p
