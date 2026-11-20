"""Alaska's ranked-choice tabulations for the November 3, 2026 general election.

The Division of Elections publishes an RCV Detailed Report for every race in which no candidate won a majority of
first choices: each round's running totals, the blank, exhausted, and overvoted ballots, and the transfer of each
eliminated candidate's ballots. The contests, options, and district units already exist; the package attaches one
result set to each contest with the rounds as values and the transfers as flows.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from uuid import uuid5

import duckdb
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "us.ak_rcv_2026"
DAY = "2026-11-03"
BASE = "https://www.elections.alaska.gov/enr/26GENR/results/"
SITE = "https://www.elections.alaska.gov/election-results/"
SUMMARY = BASE + "ElectionSummaryReportRPT.pdf"
PUBLISHER = "Alaska Division of Elections"
TITLE = "RCV Detailed Reports, November 3, 2026 General Election"
REFERENCE = DATA / "reference" / "us"
ALASKA = timezone(timedelta(hours=-9))
REPORTS = ["USRep", "SenC", "SenM", "SenO", "SenS", *(f"HD{n}" for n in (23, 24, 25, 28, 29, 30, 33, 37, 38, 39))]
KEY = f"{SOURCE}/2026-alaska-general-election"
METHOD = (
    "The Division of Elections' ranked-choice tabulation from its RCV Detailed Report, which it marks unofficial "
    "until the State Review Board certifies the election. Each candidate's value at round n is their running total; "
    "an eliminated candidate has 0 at the round after their elimination. Ballots exhausted in a round are the "
    "report's blank ballots, exhausted ballots, and overvotes together. Write-in votes are set aside before the "
    "first round, so a ballot that ranks a write-in counts for its next choice, and first-round totals can exceed "
    "the first-choice totals in the Division's election summary. The ballots cast in round 0 are the first round's "
    "continuing and non-transferable ballots. Each flow at "
    "round n is a transfer from the candidate eliminated after round n - 1, as the report prints it; transfers to "
    "exhausted ballots and overvotes have no option."
)
HEADER = re.compile(r"\d\d/\d\d/\d{4} \d\d:\d\d:\d\d|Page: \d+ / \d+|State of Alaska")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def url(report: str) -> str:
    return f"{BASE}RCV-{report}.pdf"


def fetch() -> None:
    for report in REPORTS:
        raw.fetch(SOURCE, url(report), suffix=".pdf")
    raw.fetch(SOURCE, SUMMARY, suffix=".pdf")


def number(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Round:
    votes: dict[str, int]
    continuing: int
    blanks: int
    exhausted: int
    overvotes: int
    # Transfers out of this round's eliminated candidate: destination (None for exhausted) -> ballots.
    transfers: dict[str | None, int] = field(default_factory=dict)
    eliminated: str | None = None

    @property
    def dropped(self) -> int:
        return self.blanks + self.exhausted + self.overvotes


@dataclass
class Report:
    name: str
    stamp: datetime
    rounds: list[Round]
    winner: str


def read(report: str) -> Report:
    with pymupdf.open(raw.cached(SOURCE, url(report)).path) as document:
        lines = [
            line.strip()
            for page in document
            for line in str(page.get_text()).splitlines()
            if line.strip() and not HEADER.fullmatch(line.strip())
        ]
    if "Unofficial results" not in lines[:12] or lines[lines.index("Tabulation status:") + 1] != "All Positions Filled":
        raise ValueError(f"{report}: not a complete tabulation")
    name = lines[lines.index("General Election") + 1]
    stamp = datetime.strptime(lines[lines.index("Tabulation time:") + 1], "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=ALASKA)
    rounds: list[Round] = []
    winner = None
    at = lines.index("Round 1")
    while at < len(lines):
        line = lines[at]
        if re.fullmatch(r"Round \d+", line):
            if int(line.split()[1]) != len(rounds) + 1 or lines[at + 1 : at + 4] != [
                "Candidate",
                "Votes",
                "Percentage",
            ]:
                raise ValueError(f"{report}: unexpected layout at {line}")
            at += 4
            votes: dict[str, int] = {}
            while lines[at] != "Continuing Ballots Total":
                votes[lines[at]] = number(lines[at + 1])
                if not lines[at + 2].endswith("%"):
                    raise ValueError(f"{report}: no percentage for {lines[at]}")
                at += 3
            labels = ["Continuing Ballots Total", "Blanks", "Exhausted", "Overvotes", "Non Transferable Total"]
            if lines[at : at + 10 : 2] != labels:
                raise ValueError(f"{report}: unexpected totals in {line}")
            continuing, blanks, exhausted, overvotes, dropped = (number(text) for text in lines[at + 1 : at + 10 : 2])
            this = Round(votes, continuing, blanks, exhausted, overvotes)
            if sum(votes.values()) != continuing or this.dropped != dropped:
                raise ValueError(f"{report}: {line} totals don't add up")
            rounds.append(this)
            at += 10
        elif found := re.fullmatch(r"(.+) is eliminated because the candidate had the least amount of votes\.", line):
            rounds[-1].eliminated = found[1]
            at += 1
        elif found := re.fullmatch(r"(\d+) ballots have been transferred in the following manner:", line):
            header = ["Transferred from", "Transferred to", "Ballots", "Fraction", "Votes"]
            if lines[at + 1 : at + 6] != header:
                raise ValueError(f"{report}: unexpected transfer header")
            at += 6
            moved = 0
            origin = rounds[-1].eliminated or ""
            while at < len(lines) and lines[at].startswith(origin):
                # A long pair of names shares one line: "<from> <to>".
                if lines[at] == origin:
                    target, ballots, fraction, votes_moved = lines[at + 1 : at + 5]
                    at += 1
                else:
                    target = lines[at].removeprefix(origin + " ")
                    ballots, fraction, votes_moved = lines[at + 1 : at + 4]
                if fraction != "1" or number(ballots) != number(votes_moved):
                    raise ValueError(f"{report}: a fractional transfer")
                destination = None if target in ("Exhausted", "Overvotes") else target
                rounds[-1].transfers[destination] = rounds[-1].transfers.get(destination, 0) + number(ballots)
                moved += number(ballots)
                at += 4
            if moved != int(found[1]) or moved != rounds[-1].votes[rounds[-1].eliminated or ""]:
                raise ValueError(f"{report}: transfers don't sum to the eliminated candidate's votes")
        elif found := re.fullmatch(r"(.+) is elected because .*", line):
            winner = found[1]
            at += 1
        else:
            at += 1
    if winner is None:
        raise ValueError(f"{report}: no winner")
    for before, after in pairwise(rounds):
        if before.eliminated is None:
            raise ValueError(f"{report}: a round without an elimination")
        for candidate, tally in before.votes.items():
            expected = 0 if candidate == before.eliminated else tally + before.transfers.get(candidate, 0)
            if after.votes.get(candidate, 0) != expected:
                raise ValueError(f"{report}: {candidate}'s transfer doesn't reach the next round")
        if after.dropped != before.dropped + before.transfers.get(None, 0):
            raise ValueError(f"{report}: exhausted transfers don't reach the next round")
    final = {candidate: votes for candidate, votes in rounds[-1].votes.items() if votes}
    if len(final) != 2 or max(final, key=lambda candidate: final[candidate]) != winner:
        raise ValueError(f"{report}: the winner doesn't lead the last round")
    return Report(name, stamp, rounds, winner)


def summary() -> dict[str, tuple[int, dict[str, int]]]:
    """Ballots cast and first-choice votes in each race, from the Division's election summary."""
    with pymupdf.open(raw.cached(SOURCE, SUMMARY).path) as document:
        lines = [
            line.strip() for page in document for line in str(page.get_text(sort=True)).splitlines() if line.strip()
        ]
    if "UNOFFICIAL RESULTS" not in lines[:8]:
        raise ValueError("the summary is not the Division's final unofficial count")
    races: dict[str, tuple[int, dict[str, int]]] = {}
    for at, line in enumerate(lines):
        if not line.startswith("Times Cast") or not lines[at - 2].startswith("Precincts Reported"):
            continue
        title = lines[at - 3]
        cast = number(line.split()[2])
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
        races.setdefault(title, (cast, votes))
    return races


def contest_name(report: str) -> str:
    if report == "USRep":
        return "2026 U.S. House election in Alaska's at-large congressional district"
    if report.startswith("Sen"):
        return f"2026 Alaska Senate election, {report.removeprefix('Sen')}"
    return f"2026 Alaska House of Representatives election, District {report.removeprefix('HD')}"


def surname(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.split(",")[0].lower())


@dataclass
class Contest:
    id: str
    election: str
    unit: str
    options: dict[str, str]  # ballot label -> option id


def reference() -> tuple[str, dict[str, Contest]]:
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("polity", "election", "contest", "contest_option", "reporting_unit", "result_set")
    }
    (polity,) = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-AK'").fetchone() or (None,)
    if polity is None:
        raise LookupError("the reference holds no US-AK polity")
    contests: dict[str, Contest] = {}
    for report in REPORTS:
        rows = con.execute(
            f"""select c.id, c.election_id, c.district_area_id from '{path["contest"]}' c
                join '{path["election"]}' e on e.id = c.election_id
                where e.polity_id = ? and e.date_from = date '{DAY}' and e.kind = 'general' and c.name = ?""",
            [polity, contest_name(report)],
        ).fetchall()
        if len(rows) != 1:
            raise LookupError(f"{report}: {len(rows)} contests named {contest_name(report)}")
        contest, election, area = rows[0]
        units = con.execute(
            f"""select id from '{path["reporting_unit"]}' where polity_id = ? and area_id = ? and kind = 'standard'
                and scheme_code in ('us.cd', 'us.sldl', 'us.sldu') and valid_to is null""",
            [polity, area],
        ).fetchall()
        if len(units) != 1:
            raise LookupError(f"{report}: {len(units)} reporting units for the contest's district")
        options = dict(
            con.execute(
                f"""select ballot_label, id from '{path["contest_option"]}'
                    where contest_id = ? and kind = 'candidate'""",
                [contest],
            ).fetchall()
        )
        (held,) = con.execute(
            f"select count(*) from '{path['result_set']}' where contest_id = ? and is_default", [contest]
        ).fetchone() or (0,)
        if held:
            raise ValueError(f"{report}: the contest already has a default result set")
        contests[report] = Contest(contest, election, units[0][0], options)
    return polity, contests


def match(report: str, candidate: str, options: dict[str, str]) -> str:
    last = surname(candidate)
    found = [option for label, option in options.items() if last in re.sub(r"[^a-z]", "", label.lower())]
    if len(found) != 1:
        raise LookupError(f"{report}: {len(found)} options for {candidate}")
    return found[0]


def build() -> list[Package]:
    polity, contests = reference()
    races = summary()
    p = Package(
        KEY,
        SOURCE,
        "Alaska's ranked-choice tabulations for the November 3, 2026 general election: every round and transfer for "
        "the U.S. House race and the state senate and house races that went past the first round",
    )
    src = key("source", "rcv")
    p.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=SITE,
        license="Public record",
        attribution=PUBLISHER,
    )
    for report in REPORTS:
        p.artifact(raw.cached(SOURCE, url(report)), src)
    p.artifact(raw.cached(SOURCE, SUMMARY), src)
    p.external.add(polity)
    for report in REPORTS:
        parsed = read(report)
        contest = contests[report]
        first = parsed.rounds[0]
        ballots = first.continuing + first.dropped
        cast, first_choices = races[parsed.name]
        if ballots > cast or any(
            sum(
                amount
                for row, amount in first_choices.items()
                if row.startswith(candidate.split(",")[0] + ",") or f" {candidate.split(',')[0]}," in row
            )
            not in range(1, votes + 1)
            for candidate, votes in first.votes.items()
        ):
            raise ValueError(f"{report}: the first round falls short of the summary's first choices")
        ids = {candidate: match(report, candidate, contest.options) for candidate in first.votes}
        if sorted(ids.values()) != sorted(contest.options.values()):
            raise LookupError(f"{report}: the contest has options the report lacks")
        p.external |= {contest.id, contest.election, contest.unit, *ids.values()}
        result_set = key("result_set", report)
        p.add(
            "result_set",
            id=result_set,
            election_id=contest.election,
            contest_id=contest.id,
            stage="preliminary",
            kind="counted",
            source_id=src,
            status="active",
            is_default=True,
            completeness=1.0,
            as_of=parsed.stamp,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")

        def value(option: str | None, measure: int, count_round: int, amount: int) -> None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=contest.unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=count_round,
                flags=0,
                value=amount,
            )

        value(None, 3, 0, ballots)
        value(None, 4, 1, first.continuing)
        out: set[str] = set()
        for number_, this in enumerate(parsed.rounds, 1):
            for candidate, votes in this.votes.items():
                if candidate not in out or votes:
                    value(ids[candidate], 1, number_, votes)
            out = {candidate for candidate in this.votes if not this.votes[candidate]}
            value(None, 21, number_, this.dropped)
            if number_ > 1:
                before = parsed.rounds[number_ - 2]
                for destination, moved in before.transfers.items():
                    p.add(
                        "result_flow",
                        result_set_id=result_set,
                        unit_id=contest.unit,
                        from_option_id=ids[before.eliminated or ""],
                        option_id=None if destination is None else ids[destination],
                        channel=0,
                        measure=21 if destination is None else 1,
                        count_round=number_,
                        flags=0,
                        value=moved,
                    )
        last = parsed.rounds[-1].votes
        for candidate, option in ids.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if candidate == parsed.winner else "lost" if last[candidate] else "eliminated",
            )
    return [p]


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
