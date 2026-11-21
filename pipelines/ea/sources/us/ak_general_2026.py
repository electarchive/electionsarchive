"""Alaska's November 3, 2026 general election by precinct, first choices as the Division of Elections counted them.

The Division's results by precinct list every contest on the ballot for each of its precincts, with the absentee, early,
and questioned ballots reported by House district and the federal overseas ballots statewide. The election and its
contests already exist, with the ranked-choice tabulations attached as the default result sets; this pipeline mints the
2026 general-election precinct and aggregate units and attaches a non-default result set of first-choice votes at that
grain to each contest the archive holds. The Division's election summary supplies each candidate's statewide total,
which the package declares against the precinct rows.
"""

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid5

import duckdb
import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "us.ak_general_2026"
DAY = date(2026, 11, 3)
BASE = "https://www.elections.alaska.gov/enr/26GENR/results/"
PRECINCTS = BASE + "GA_ENR_Precinct_State_of_Alaska.csv"
SUMMARY = BASE + "ElectionSummaryReportRPT.pdf"
SITE = "https://www.elections.alaska.gov/election-results/"
PUBLISHER = "Alaska Division of Elections"
TITLE = "2026 General Election results by precinct"
SUMMARY_TITLE = "2026 General Election summary report"
REFERENCE = DATA / "reference" / "us"
UNITS = f"{SOURCE}/units"
RESULTS = f"{SOURCE}/general"
ELECTION = "2026 Alaska general election"
KINDS = {"Absentee": "absentee", "Question": "question", "Early Voting": "early"}
# How the Division's columns map to the archive's channels: election day, early, postal, abroad, provisional.
CHANNELS = {"Election Day": 1, "Early Voting": 2, "Absentee": 3, "Question": 5}
OVERSEAS = 4
STATEWIDE = {
    "U.S. Senator": "2026 U.S. Senate election in Alaska",
    "U.S. Representative": "2026 U.S. House election in Alaska's at-large congressional district",
    "Governor / Lieutenant Governor": "2026 Alaska gubernatorial election",
    "Supreme Court - Pate": "2026 AK Supreme Court Justice retention election",
}
MEASURES = {"registered": 2, "ballots": 3, "invalid": 5, "blank": 6}
METHOD = (
    "First-choice votes by precinct from the Division of Elections' results file, which the Division marks unofficial "
    "until the State Review Board certifies the election. Precincts report the ballots cast on election day; the "
    "absentee, early, and questioned ballots are reported by House district as their own units, and the federal "
    "overseas absentee ballots statewide. Ballots cast, overvoted ballots (as invalid), and undervoted ballots (as "
    "blank) are the file's counts for the contest, and registered voters are the precinct's. The file lists no "
    "write-in candidates, so ballots cast exceed the votes, overvotes, and undervotes by the write-in votes that the "
    "Division's election summary reports. Ranked-choice rounds, where a race went past the first choices, are in the "
    "default result set."
)
# The Division prints one candidate name differently from the archive's ballot label.
SPELLINGS: dict[str, str] = {}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    raw.fetch(SOURCE, PRECINCTS, suffix=".csv")
    raw.fetch(SOURCE, SUMMARY, suffix=".pdf")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def words(text: str) -> list[str]:
    plain = re.sub(r'"[^"]*"|“[^”]*”', "", text).casefold()
    return [word for word in re.findall(r"[a-z'-]+", plain) if word not in ("jr", "sr", "ii", "iii", "iv")]


def spell(text: str) -> str:
    last, _, given = text.partition(", ")
    return f"{given} {last}" if given else last


def label_key(label: str) -> tuple[str, str]:
    spelled = words(label)
    return spelled[0], spelled[-1]


def candidate_key(name: str) -> tuple[str, str]:
    if "/" in name:  # a governor and lieutenant governor ticket, printed by surname only
        return "", words(name.split("/")[0])[-1]
    return label_key(spell(name))


def match(options: dict[str, str], name: str) -> str:
    """The option for a name in the Division's file: by the candidate's first and last words, else by surname."""
    name = SPELLINGS.get(name, name)
    if name in ("YES", "NO"):
        return options[name.title()]
    first, last = candidate_key(name)
    found = [
        option
        for label, option in options.items()
        if label_key(label)[1] == last and (not first or label_key(label)[0] == first)
    ]
    if len(found) != 1:
        raise LookupError(f"{len(found)} options for {name}")
    return found[0]


@dataclass
class Contest:
    id: str
    name: str
    district: str | None
    options: dict[str, str]  # ballot label -> option id


@dataclass
class Reference:
    polity: str
    election: str
    state_unit: str
    state_area: str
    districts: dict[int, str]  # House district -> area id
    contests: dict[str, Contest] = field(default_factory=dict)


def reference() -> Reference:
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("polity", "area", "area_code", "reporting_unit", "contest", "contest_option", "election")
    }
    (polity,) = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-AK'").fetchone() or (None,)
    if polity is None:
        raise LookupError("the reference holds no US-AK polity")
    found = con.execute(
        f"select id from '{path['election']}' where polity_id = ? and date_from = date '{DAY}' and name = ?",
        [polity, ELECTION],
    ).fetchall()
    if len(found) != 1:
        raise LookupError(f"the reference holds {len(found)} elections named {ELECTION}")
    unit = con.execute(
        f"select id, area_id from '{path['reporting_unit']}' where polity_id = ? and scheme_code = 'us.state'",
        [polity],
    ).fetchone()
    if unit is None:
        raise LookupError("the reference holds no state unit")
    state_unit, state_area = str(unit[0]), str(unit[1])
    districts = {
        int(code.removeprefix("AK:")): area
        for area, code in con.execute(
            f"""select c.area_id, c.code from '{path["area_code"]}' c join '{path["area"]}' a on a.id = c.area_id
                where a.scheme_code = 'us.sldl' and c.code like 'AK:%'"""
        ).fetchall()
    }
    ref = Reference(polity, found[0][0], state_unit, state_area, districts)
    for contest, name, district, option, label in con.execute(
        f"""select c.id, c.name, c.district_area_id, o.id, o.ballot_label
            from '{path["contest"]}' c join '{path["contest_option"]}' o on o.contest_id = c.id
            where c.election_id = ?""",
        [ref.election],
    ).fetchall():
        ref.contests.setdefault(contest, Contest(contest, name, district, {})).options[label] = option
    return ref


def contest_name(title: str, ref: Reference) -> str | None:
    """The archive's name for a contest in the Division's file, or None for one the archive doesn't hold yet."""
    if title in STATEWIDE:
        return STATEWIDE[title]
    if found := re.fullmatch(r"Senate District ([A-T])", title):
        return f"2026 Alaska Senate election, {found[1]}"
    if found := re.fullmatch(r"House District (\d+)", title):
        return f"2026 Alaska House of Representatives election, District {found[1]}"
    if found := re.fullmatch(r"BM#(\d+) \w+", title):
        names = [c.name for c in ref.contests.values() if c.name.startswith(f"Ballot Measure {found[1]}:")]
        return names[0] if len(names) == 1 else None
    return None


@dataclass
class Place:
    code: str  # the unit's code
    name: str
    kind: str  # precinct or aggregate
    district: int | None  # House district, None for the overseas ballots
    channel: int  # the archive's channel for the place's ballots
    column: str  # the Division's column that holds them


def place_of(name: str) -> Place:
    name = name.strip()
    if precinct := re.match(r"(\d\d)-(\d\d\d) ", name):
        return Place(
            f"{DAY.year}:AK:{precinct[1]}-{precinct[2]}", name, "precinct", int(precinct[1]), 1, "Election Day"
        )
    if aggregate := re.fullmatch(r"District (\d+)\s*- (.*)", name):
        kind = aggregate[2].strip()
        return Place(
            f"AK:{DAY.year}:{int(aggregate[1]):02}:{KINDS[kind]}",
            f"District {int(aggregate[1])} {kind.lower()}",
            "aggregate",
            int(aggregate[1]),
            CHANNELS[kind],
            kind,
        )
    if name.startswith("HD99"):
        return Place(f"AK:{DAY.year}:99", "Federal overseas absentee", "aggregate", None, OVERSEAS, "Absentee")
    raise ValueError(f"unknown place {name}")


def summary() -> dict[str, dict[str, int]]:
    """Each race's candidate votes (and write-ins) from the Division's election summary, by race title."""
    with pymupdf.open(raw.cached(SOURCE, SUMMARY).path) as document:
        lines = [
            line.strip() for page in document for line in str(page.get_text(sort=True)).splitlines() if line.strip()
        ]
    if "UNOFFICIAL RESULTS" not in lines[:10]:
        raise ValueError("the summary is not the Division's unofficial count")
    races: dict[str, dict[str, int]] = {}
    for at, line in enumerate(lines):
        if not line.startswith("Times Cast") or not lines[at - 2].startswith("Precincts Reported"):
            continue
        votes: dict[str, int] = {}
        wrapped = ""  # the first line of a long name the summary wraps; its tail leads the next name's line
        for row in lines[at + 2 :]:
            if row.startswith("Total Votes"):
                break
            if found := re.fullmatch(r"(.+?)\s+([\d,]+)\s+[\d.]+%", row):
                votes[f"{wrapped} {found[1].strip()}".strip()] = number(found[2])
                wrapped = ""
            else:
                wrapped = row
        races.setdefault(lines[at - 3], votes)
    return races


def unit_id(code: str) -> str:
    return key("unit", DAY, code)


def build() -> list[Package]:
    ref = reference()
    artifact = raw.cached(SOURCE, PRECINCTS)
    totals = summary()
    names = {contest.name: contest for contest in ref.contests.values()}

    places: dict[str, Place] = {}
    registered: dict[str, int] = {}
    # title -> place code -> (candidate -> votes by channel, counts)
    votes: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    counts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    with artifact.path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            place = places.setdefault(row["Precinct_name"].strip(), place_of(row["Precinct_name"]))
            if row["Reg_voters"] and place.kind == "precinct":
                registered[place.code] = number(row["Reg_voters"])
            title = row["Contest_title"]
            channel = place.column
            if row[f"{channel}_ballots"] == "":
                raise ValueError(f"{place.code} {title}: no {channel} ballots")
            if row["total_ballots"] != row[f"{channel}_ballots"] or row["total_votes"] != row[f"{channel}_votes"]:
                raise ValueError(f"{place.code} {title}: the total isn't the {channel} column")
            votes[title][place.code][row["candidate_name"].strip()] = number(row["total_votes"])
            counts[title][place.code] = {
                "ballots": number(row["total_ballots"]),
                "blank": number(row["total_under_votes"] or "0"),
                "invalid": number(row["total_over_votes"] or "0"),
            }
    by_code = {place.code: place for place in places.values()}

    src = key("source", "results")
    units = Package(
        UNITS,
        SOURCE,
        "Alaska's precincts and House-district aggregates for the November 3, 2026 general election",
    )
    units.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=SITE,
        license="Public record",
        attribution=PUBLISHER,
    )
    units.artifact(artifact, src)
    units.artifact(raw.cached(SOURCE, SUMMARY), src)
    units.external |= {ref.polity, ref.state_unit, ref.state_area, "us.precinct", "us.aggregate"}
    for place in sorted(places.values(), key=lambda p: p.code):
        area = ref.state_area if place.district is None else ref.districts[place.district]
        units.external.add(area)
        units.add(
            "reporting_unit",
            id=unit_id(place.code),
            polity_id=ref.polity,
            scheme_code="us.precinct" if place.kind == "precinct" else "us.aggregate",
            code=place.code,
            parent_id=ref.state_unit,
            area_relation="within",
            area_id=area,
            kind="standard" if place.kind == "precinct" else "aggregate",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
        units.name(unit_id(place.code), place.name, "en", "official")

    p = Package(
        RESULTS,
        SOURCE,
        "First-choice results of Alaska's November 3, 2026 general election by precinct, with the absentee, early, "
        "and questioned ballots by House district",
        depends=[UNITS],
    )
    p.uses(artifact)
    p.uses(raw.cached(SOURCE, SUMMARY))
    p.external |= {ref.election, ref.state_unit}
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    skipped: list[str] = []
    for title in sorted(votes):
        name = contest_name(title, ref)
        if name is None or name not in names:
            skipped.append(title)
            continue
        contest = names[name]
        ids = {candidate: match(contest.options, candidate) for candidate in next(iter(votes[title].values()))}
        if sorted(ids.values()) != sorted(contest.options.values()):
            print(
                title,
                "options absent from the file:",
                sorted(set(contest.options.values()) - set(ids.values())),
                [l for l, o in contest.options.items() if o not in ids.values()],
            )
        p.external |= {contest.id, *ids.values()}
        result_set = key("result_set", contest.id, "precinct")
        p.add(
            "result_set",
            id=result_set,
            election_id=ref.election,
            contest_id=contest.id,
            stage="preliminary",
            kind="counted",
            source_id=src,
            status="active",
            is_default=False,
            completeness=1.0,
            as_of=artifact.retrieved_at,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        by_candidate: dict[str, int] = defaultdict(int)
        for code, by_name in votes[title].items():
            channel = by_code[code].channel
            for candidate, amount in by_name.items():
                rows.append((result_set, unit_id(code), ids[candidate], channel, 1, amount))
                by_candidate[candidate] += amount
            for measure, amount in counts[title][code].items():
                rows.append((result_set, unit_id(code), None, channel, MEASURES[measure], amount))
            if code in registered:
                rows.append((result_set, unit_id(code), None, 0, MEASURES["registered"], registered[code]))
        published = totals[title]
        for candidate, total in by_candidate.items():
            base = re.sub(r'\s*"[^"]*"', "", candidate).strip()
            found = [amount for label, amount in published.items() if base in label]
            if len(found) != 1:
                raise LookupError(f"{title}: {candidate} in the summary: {list(published)}")
            declared = found[0]
            if declared != total:
                raise ValueError(f"{title}: {candidate} sums to {total}, the summary says {declared}")
            p.add("declared", result_set_id=result_set, option_id=ids[candidate], measure=1, value=total)
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
    print("left out:", len(skipped), sorted(skipped))
    return [units, p]


def packages() -> list[str]:
    return [UNITS, RESULTS]


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
