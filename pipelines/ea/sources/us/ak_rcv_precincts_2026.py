"""Alaska's November 3, 2026 ranked-choice rounds by precinct, tabulated from the Division's cast vote records.

The Division publishes each ranked-choice race's rounds for the whole district only. This pipeline tabulates the
cast vote records for the fifteen races that went past the first choices, precinct by precinct, with the rules the
Division's own count follows, and attaches one non-default result set to each contest on the 2026 precinct and
aggregate units of the first-choice precinct package. Summed over the district, every round reproduces the
Division's RCV Detailed Report exactly, and each race's transfers do too.
"""

import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass

import duckdb
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.us import ak_general_2026 as general
from ea.sources.us import ak_rcv_2026 as rcv
from ea.store import DATA

SOURCE = "us.ak_rcv_precincts_2026"
RECORDS = rcv.BASE + "CVR_Export.zip"
TITLE = "Cast vote records, November 3, 2026 General Election"
REFERENCE = DATA / "reference" / "us"
KEY = f"{SOURCE}/rounds"
METHOD = (
    "Tabulated by electionsarchive.com from the Division of Elections' cast vote records, precinct by precinct, "
    "which the Division marks unofficial until the State Review Board certifies the election. A ballot exhausts at "
    "an overvote or after two skipped rankings in a row, a repeated candidate counts as a skipped ranking, and "
    "write-ins are passed over, as the Division's tabulation has them. Each round drops the candidates the "
    "Division's count dropped. Each candidate's value at round n is their running total; an eliminated candidate "
    "has 0 at the round after their elimination. Ballots exhausted in a round are blank ballots, exhausted ballots, "
    "and overvotes together. Precincts report their election-day ballots; absentee, early, and questioned ballots "
    "are counted by state house district and overseas federal absentee ballots statewide, as the Division reports "
    "them. Summed over the district, every round and every transfer reproduces the Division's RCV Detailed Report."
)


def fetch() -> None:
    rcv.fetch()
    raw.fetch(SOURCE, RECORDS, suffix=".zip")


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r'"[^"]*"', "", text).lower())


TITLES = {
    "USRep": "U.S. Representative",
    **{f"Sen{x}": f"Senate District {x}" for x in "CMOS"},
    **{f"HD{n}": f"House District {n}" for n in (23, 24, 25, 28, 29, 30, 33, 37, 38, 39)},
}


@dataclass
class Ballots:
    # contest description -> (unit code, ranking as tuples of candidate names per rank) -> ballots
    tallies: dict[str, Counter[tuple[str, tuple[tuple[str, ...], ...]]]]


def read_records() -> Ballots:
    with zipfile.ZipFile(raw.cached(SOURCE, RECORDS).path) as archive:
        manifest = {
            name: json.loads(archive.read(f"{name}Manifest.json"))["List"]
            for name in ("Contest", "Candidate", "Precinct", "PrecinctPortion", "OutstackCondition")
        }
        invalid = {c["Id"] for c in manifest["OutstackCondition"] if c["Description"] == "InvalidContest"}
        descriptions = {c["Id"]: re.sub(r"\s+", " ", c["Description"]).strip() for c in manifest["Contest"]}
        wanted = {cid: description for cid, description in descriptions.items() if description in TITLES.values()}
        if len(wanted) != len(rcv.REPORTS):
            raise ValueError(f"the records hold {len(wanted)} of the {len(rcv.REPORTS)} races")
        candidates = {c["Id"]: c["Description"] for c in manifest["Candidate"]}
        precincts = {c["Id"]: c["Description"] for c in manifest["Precinct"]}
        units = {}
        for portion in manifest["PrecinctPortion"]:
            units[portion["Id"]] = general.place_of(precincts[portion["PrecinctId"]]).code
        tallies: dict[str, Counter[tuple[str, tuple[tuple[str, ...], ...]]]] = {d: Counter() for d in wanted.values()}
        for member in archive.namelist():
            if not member.startswith("CvrExport"):
                continue
            for session in json.loads(archive.read(member))["Sessions"]:
                side = session.get("Modified") or session["Original"]
                for card in side["Cards"]:
                    for contest in card["Contests"]:
                        if contest["Id"] not in wanted or invalid & set(contest["OutstackConditionIds"]):
                            continue
                        ranks: dict[int, set[str]] = {}
                        for mark in contest["Marks"]:
                            if not mark["IsAmbiguous"]:
                                ranks.setdefault(mark["Rank"], set()).add(candidates[mark["CandidateId"]])
                        ranking = tuple(tuple(sorted(ranks.get(r, ()))) for r in range(1, max(ranks, default=0) + 1))
                        tallies[wanted[contest["Id"]]][(units[side["PrecinctPortionId"]], ranking)] += 1
    return Ballots(tallies)


def top(ranking: tuple[tuple[str, ...], ...], standing: set[str], alias: dict[str, str]) -> str | None:
    """The candidate a ballot counts for among those standing, under the Division's exhaustion rules."""
    skips = 0
    seen: set[str] = set()
    for marks in ranking:
        if not marks:
            skips += 1
            if skips == 2:
                return None
            continue
        if len(marks) > 1:
            return None
        choice = alias.get(plain(marks[0]), marks[0])
        if choice in seen:
            skips += 1
            if skips == 2:
                return None
            continue
        skips = 0
        seen.add(choice)
        if choice in standing:
            return choice
    return None


@dataclass
class Tally:
    # (unit, round, candidate or None) -> ballots, and (unit, round, from, to) -> ballots transferred
    votes: Counter[tuple[str, int, str | None]]
    flows: Counter[tuple[str, int, str, str | None]]


def tabulate(report: str, parsed: rcv.Report, ballots: Counter[tuple[str, tuple[tuple[str, ...], ...]]]) -> Tally:
    labels = list(parsed.rounds[0].votes)
    alias = {plain(label): label for label in labels}
    if len(alias) != len(labels):
        raise ValueError(f"{report}: two candidates share a plain name")
    standing: list[set[str]] = []
    gone: set[str] = set()
    for this in parsed.rounds:
        standing.append(set(labels) - gone)
        if this.eliminated is not None:
            gone.add(this.eliminated)
    votes: Counter[tuple[str, int, str | None]] = Counter()
    flows: Counter[tuple[str, int, str, str | None]] = Counter()
    for (unit, ranking), count in ballots.items():
        before: str | None = None
        for number, this in enumerate(standing, 1):
            choice = top(ranking, this, alias)
            votes[(unit, number, choice)] += count
            if number > 1 and before is not None and before == parsed.rounds[number - 2].eliminated:
                flows[(unit, number, before, choice)] += count
            before = choice
    return Tally(votes, flows)


def check(report: str, parsed: rcv.Report, found: Tally) -> None:
    for number, this in enumerate(parsed.rounds, 1):
        sums: Counter[str | None] = Counter()
        for (_, n, choice), count in found.votes.items():
            if n == number:
                sums[choice] += count
        official = {label: votes for label, votes in this.votes.items() if votes}
        got = {label: votes for label, votes in sums.items() if label is not None and votes}
        if got != official or sums[None] != this.dropped:
            raise ValueError(
                f"{report} round {number}: records give {dict(sums)}, the report {this.votes} and {this.dropped} dropped"
            )
        if number > 1:
            before = parsed.rounds[number - 2]
            moved: Counter[str | None] = Counter()
            for (_, n, origin, destination), count in found.flows.items():
                if n == number:
                    if origin != before.eliminated:
                        raise ValueError(f"{report}: a transfer from {origin}")
                    moved[destination] += count
            if dict(moved) != before.transfers:
                raise ValueError(f"{report} round {number}: transfers {dict(moved)}, the report {before.transfers}")


@dataclass
class Contest:
    id: str
    election: str
    options: dict[str, str]


def reference() -> dict[str, Contest]:
    con = duckdb.connect()
    path = {name: str(REFERENCE / f"{name}.parquet") for name in ("polity", "election", "contest", "contest_option")}
    (polity,) = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-AK'").fetchone() or (None,)
    if polity is None:
        raise LookupError("the reference holds no US-AK polity")
    contests: dict[str, Contest] = {}
    for report in rcv.REPORTS:
        rows = con.execute(
            f"""select c.id, c.election_id from '{path["contest"]}' c join '{path["election"]}' e on e.id = c.election_id
                where e.polity_id = ? and e.date_from = date '{rcv.DAY}' and e.kind = 'general' and c.name = ?""",
            [polity, rcv.contest_name(report)],
        ).fetchall()
        if len(rows) != 1:
            raise LookupError(f"{report}: {len(rows)} contests named {rcv.contest_name(report)}")
        options = dict(
            con.execute(
                f"select ballot_label, id from '{path['contest_option']}' where contest_id = ? and kind = 'candidate'",
                [rows[0][0]],
            ).fetchall()
        )
        contests[report] = Contest(rows[0][0], rows[0][1], options)
    return contests


def build() -> list[Package]:
    units, _results = general.build()
    in_units = {str(row["code"]): str(row["id"]) for row in units.rows["reporting_unit"]}
    contests = reference()
    ballots = read_records()
    p = Package(
        KEY,
        SOURCE,
        "Alaska's ranked-choice rounds for the November 3, 2026 general election by precinct, tabulated from the "
        "cast vote records",
        depends=[general.UNITS],
    )
    src = p.id("source", "cvr")
    p.add(
        "source",
        id=src,
        kind="official_body",
        publisher=rcv.PUBLISHER,
        title=TITLE,
        url=rcv.SITE,
        license="Public record",
        attribution=rcv.PUBLISHER,
    )
    records = raw.cached(SOURCE, RECORDS)
    p.artifact(records, src)
    for report in rcv.REPORTS:
        p.uses(raw.cached(rcv.SOURCE, rcv.url(report)))
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    flow_rows: list[tuple[str, str, str, str | None, int, int, int]] = []
    for report in rcv.REPORTS:
        parsed = rcv.read(report)
        contest = contests[report]
        counter = ballots.tallies[TITLES[report]]
        found = tabulate(report, parsed, counter)
        check(report, parsed, found)
        ids = {label: rcv.match(report, label, contest.options) for label in parsed.rounds[0].votes}
        if sorted(ids.values()) != sorted(contest.options.values()):
            raise LookupError(f"{report}: the contest has options the report lacks")
        p.external |= {contest.id, contest.election, *ids.values()}
        result_set = p.id("result_set", report)
        p.add(
            "result_set",
            id=result_set,
            election_id=contest.election,
            contest_id=contest.id,
            stage="preliminary",
            kind="counted",
            source_id=src,
            status="active",
            is_default=False,
            completeness=1.0,
            as_of=records.retrieved_at,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        by_place: dict[str, Counter[tuple[int, str | None]]] = defaultdict(Counter)
        for (place, number, choice), count in found.votes.items():
            by_place[place][(number, choice)] += count
        places = sorted(by_place)
        for place in places:
            if place not in in_units:
                raise LookupError(f"{report}: no 2026 unit for {place}")
        eliminated_at = {this.eliminated: number for number, this in enumerate(parsed.rounds, 1) if this.eliminated}
        for place in places:
            unit = in_units[place]
            first = by_place[place]
            total = sum(c for (n, _), c in first.items() if n == 1)
            continuing = total - first[(1, None)]
            rows.append((result_set, unit, None, 3, 0, total))
            rows.append((result_set, unit, None, 4, 1, continuing))
            for number in range(1, len(parsed.rounds) + 1):
                dropped = first[(number, None)]
                rows.append((result_set, unit, None, 21, number, dropped))
                for label in parsed.rounds[0].votes:
                    gone = eliminated_at.get(label)
                    if gone is not None and number > gone + 1:
                        continue
                    rows.append((result_set, unit, ids[label], 1, number, first[(number, label)]))
        for (place, number, origin, destination), count in sorted(found.flows.items(), key=str):
            flow_rows.append(
                (
                    result_set,
                    in_units[place],
                    ids[origin],
                    None if destination is None else ids[destination],
                    21 if destination is None else 1,
                    number,
                    count,
                )
            )
        p.external |= {in_units[place] for place in places}
        last = parsed.rounds[-1].votes
        for label, option in ids.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if label == parsed.winner else "lost" if last[label] else "eliminated",
            )
    p.frame(
        "result_value",
        pl.DataFrame(
            [(a, b, c, 0, d, e, 0, f) for a, b, c, d, e, f in rows],
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
    p.frame(
        "result_flow",
        pl.DataFrame(
            [(a, b, c, d, 0, e, f, 0, g) for a, b, c, d, e, f, g in flow_rows],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "from_option_id": pl.String,
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
    return [units, p]


def packages() -> list[str]:
    return [general.UNITS, KEY]


def parse() -> None:
    fetch()
    general.fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
