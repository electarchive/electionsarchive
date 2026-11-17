"""The 1993, 1996, and 1998 federal elections, from the Australian Electoral Commission's election statistics CD-ROM.

The CD-ROM's import folder holds machine-readable tables per election: candidates, division totals, the House
distribution of preferences by polling place, and Senate first preferences by polling place. The election packages
here extend the au.aec pipeline backward from the Tally Room archive's first election in 2001.
"""

import csv
import functools
import io
import re
import zipfile
from collections import Counter, defaultdict
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import changeset, raw
from ea.package import Package
from ea.sources.au import aec as base
from ea.sources.au.aec import (
    AUSTRALIA,
    COUNTRY,
    HOUSE,
    HOUSE_SYSTEM,
    SENATE,
    SENATE_GVT,
    SOURCE,
    STATE_NAMES,
    STATES,
    Units,
    Values,
    area,
    key,
)

ZIP = "https://www.aec.gov.au/About_AEC/Publications/statistics/files/aec-1993-1996-1998-election-statistics.zip"


class Year(NamedTuple):
    year: int
    day: date
    code: str


YEARS = [
    Year(1993, date(1993, 3, 13), "93"),
    Year(1996, date(1996, 3, 2), "96"),
    Year(1998, date(1998, 10, 3), "98"),
]
DAYS = [y.day for y in YEARS]
SPECIAL = {"ABSENT": "ABSENT", "POSTAL": "POSTAL", "PRE POLL": "PRE_POLL", "PROVISIONAL": "PROVISIONAL"}
STATE_BY_NAME = {name.lower(): ab for ab, name in STATE_NAMES.items()}
HOUSE_METHOD = (
    "First preferences per polling place from the Australian Electoral Commission's election statistics tables "
    "of the distribution of preferences by polling place. Ordinary votes are reported per polling place; absent, "
    "postal, pre-poll, and provisional votes are reported once per division and stored as one unit each. The later "
    "counts of the distribution of preferences are not stored; the outcomes are the declared member and the final two "
    "candidates of the count. Informal votes come from the informal votes by polling place table{informal}. "
    "Enrolment is stored on a separate unit per division."
)
SENATE_METHOD = (
    "First preferences per polling place from the Australian Electoral Commission's election statistics tables. "
    "Votes above the line are stored on one option per group and votes below the line on each candidate, so the "
    "options together hold every formal vote. Absent, postal, pre-poll, and provisional votes are published once "
    "per division and are stored as one unit each. Informal ballots are stored per polling place. The distribution "
    "of preferences is not stored. Enrolment is stored on a separate unit per division."
)


def member(year: Year, folder: str, name: str) -> str:
    return f"data/{folder}/tables{year.code}/{name}"


@functools.cache
def archive() -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, ZIP).path)


def text(path: str) -> str:
    data = archive().read(path)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


@functools.cache
def table(year: Year, name: str) -> list[dict[str, str]]:
    """One semicolon-delimited table of the CD-ROM's import folder."""
    reader = csv.DictReader(io.StringIO(text(member(year, "import", f"{name}.TXT")), newline=""), delimiter=";")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def number(value: str) -> int:
    return int(value.replace(",", "") or 0)


def fetch() -> None:
    raw.fetch(SOURCE, ZIP, suffix=".zip")


# Geography and parties -------------------------------------------------------------------------------------------


def division_names() -> list[tuple[Year, str, str]]:
    """Every (election, state, division name) of the three elections' division tables."""
    return [(y, row["State"], row["Division"]) for y in YEARS for row in table(y, "DIVS")]


@functools.cache
def party_names() -> dict[str, str]:
    """Registered party codes and names from the CD-ROM's code table."""
    names: dict[str, str] = {}
    for line in text("data/geninfo/tables98/text/CODE.TXT").splitlines():
        cells = [c.strip().strip('"') for c in line.split("\t")]
        if len(cells) >= 2 and re.fullmatch(r"[A-Z0-9]{2,4}", cells[0]) and cells[1]:
            names.setdefault(cells[0], cells[1])
    for y in YEARS:
        for row in table(y, "HCANDS"):
            if row["Party"] not in names and row["Candidate"]:
                names.setdefault(row["Party"], "")
    return {ab: name for ab, name in names.items() if name and ab not in base.NO_PARTY}


# Elections -------------------------------------------------------------------------------------------------------


def _by_state(year: Year) -> dict[str, dict[str, int]]:
    """Senate FORMAL and INFORMAL counts and the enrolment by state from the first preferences by group table."""
    out: dict[str, dict[str, int]] = {}
    current = None
    for line in text(member(year, "senres", "text/V1_15.TXT")).splitlines():
        cells = [c.strip() for c in line.split("\t")]
        if cells[0].lower() in STATE_BY_NAME:
            current = STATE_BY_NAME[cells[0].lower()]
            out[current] = {}
        elif current and cells[0].startswith("Enrolment "):
            out[current]["ENROLMENT"] = number(cells[0].split()[1])
        elif current and cells[0] in ("FORMAL", "INFORMAL") and len(cells) > 1:
            out[current][cells[0]] = number(cells[1])
    return out


def _blocks(
    rows: list[dict[str, str]], count_key: str | None = None
) -> list[tuple[str, str, int, list[dict[str, str]]]]:
    """Group a table's rows into (division, place, repeat number, rows), splitting places that share a name."""
    blocks: list[tuple[str, str, int, list[dict[str, str]]]] = []
    seen: Counter[tuple[str, str]] = Counter()
    seen_keys: set[tuple[str, ...]] = set()
    for row in rows:
        division, place = row["Division"], row["Polling Place"]
        row_key = (row["Ticket"], row["Ballot Position"]) if "Ticket" in row else (row["Count"], row["Ballot Position"])
        if not blocks or blocks[-1][:2] != (division, place) or row_key in seen_keys:
            if blocks and blocks[-1][:2] == (division, place):
                seen[(division, place)] += 1
            else:
                seen[(division, place)] = seen.get((division, place), 0)
            blocks.append((division, place, seen[(division, place)], []))
            seen_keys = set()
        seen_keys.add(row_key)
        blocks[-1][3].append(row)
    return blocks


def _place_id(division: str, place: str, repeat: int) -> str:
    return f"{division}:{place}" + (f"#{repeat + 1}" if repeat else "")


def _candidate(
    p: Package, year: Year, contest: str, tag: str, label: str, position: int, ab: str, known: set[str], senate: bool
) -> str:
    where = "senate" if senate else "house"
    option = key("option", f"cd{year.year}", where, tag)
    party_id = base._party_of(ab, known)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=party_id,
        ballot_position=position,
        ballot_label=label,
    )
    surname, _, given = label.partition(", ")
    person = key("person", f"cd{year.year}", where, tag)
    p.add("person", id=person)
    p.name(person, base.person_name(given, surname), "en", "official")
    p.add(
        "candidacy",
        id=key("candidacy", f"cd{year.year}", where, tag),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=label,
        member_of_party_id=party_id,
        is_independent=ab in base.NO_PARTY,
    )
    return option


def house(p: Package, year: Year, election: str, path: str, units: Units, values: Values, known: set[str]) -> int:
    ids = base.division_ids()
    divisions = {row["Division"]: row for row in table(year, "DIVS")}

    def code_of(division: str) -> str:
        return ids[(divisions[division]["State"], division.lower())]

    candidates: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in table(year, "HCANDS"):
        if row["Candidate"]:
            candidates[row["Division"]][row["Ballot Position"]] = row
    dop = table(year, "HPPDOP")
    informal: dict[tuple[str, str, int], int] = {}
    for division, place, repeat, rows in _blocks(
        [{**r, "Ticket": "", "Ballot Position": "0"} for r in table(year, "HTPPBYPP")]
    ):
        informal[(division, place, repeat)] = sum(number(r["Informal Vote"]) for r in rows)
    first: dict[str, list[tuple[str, int, dict[str, int]]]] = defaultdict(list)
    final: dict[str, dict[str, str]] = defaultdict(dict)
    last: dict[str, int] = {}
    for row in dop:
        if row["Polling Place"] == "DIVISION SUMMARY":
            last[row["Division"]] = max(last.get(row["Division"], 0), number(row["Count"]))
    for row in dop:
        if row["Polling Place"] == "DIVISION SUMMARY" and number(row["Count"]) == last[row["Division"]]:
            final[row["Division"]][row["Ballot Position"]] = row["Candidate Status"]
    places = [
        (d, pl, n, rows)
        for d, pl, n, rows in _blocks([r for r in dop if r["Count"] == "1"])
        if pl != "DIVISION SUMMARY"
    ]
    for division, place, repeat, rows in places:
        first[division].append((place, repeat, {r["Ballot Position"]: number(r["Vote"]) for r in rows}))
    for division, name in sorted((d, d) for d in divisions):
        row = divisions[division]
        state = row["State"]
        code = ids[(state, division.lower())]
        cands = candidates[division]
        contest = key("contest", f"cd{year.year}", "house", code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=area("au.division", code),
            magnitude=1,
            round_number=1,
            electoral_system_id=HOUSE_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{name} ({state})", "en", "official")
        p.add("slug", path=f"{path}/house-{changeset.slug(name)}", entity_id=contest, is_current=True)
        has_informal = any(k[0] == division for k in informal) and any(
            informal[k] for k in informal if k[0] == division
        )
        result_set = base._result_set(
            p,
            election,
            contest,
            HOUSE_METHOD.format(
                informal=""
                if has_informal
                else "; the source gives no informal count per polling place for this division"
            ),
        )
        options: dict[str, str] = {}
        for position, entry in sorted(cands.items(), key=lambda item: int(item[0])):
            options[position] = _candidate(
                p, year, contest, f"{code}:{position}", entry["Candidate"], int(position), entry["Party"], known, False
            )
        standing = {pos for pos, status in final[division].items() if status in ("E", "R")}
        winners = [pos for pos, status in final[division].items() if status == "E"]
        if len(winners) != 1:
            raise ValueError(f"{year.year} {division}: {len(winners)} candidates marked elected")
        marked = [pos for pos, entry in cands.items() if entry["Member"] == "#"]
        if marked != winners:
            raise ValueError(f"{year.year} {division}: member {marked} differs from the count's winner {winners}")
        for position, option in options.items():
            kind = "elected" if position in winners else "lost" if position in standing else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        valid: Counter[str] = Counter()
        bad: dict[str, int] = {}
        for place, repeat, votes in first[division]:
            if place in SPECIAL:
                unit = units.declaration(code_of(division), SPECIAL[place])
            else:
                unit = units.place(code_of(division), _place_id(division, place, repeat), place)
            for position, vote in votes.items():
                if position not in options:
                    raise ValueError(f"{year.year} {division} {place}: ballot position {position} is not a candidate")
                values.add(result_set, unit, options[position], 1, vote)
                valid[unit] += vote
            if has_informal and (division, place, repeat) in informal:
                bad[unit] = informal[(division, place, repeat)]
        for position, option in options.items():
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=1,
                value=number(cands[position]["Vote"]),
            )
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=number(row["Formal Vote"]))
        if has_informal:
            p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=number(row["Informal Vote"]))
        for unit, n in valid.items():
            values.add(result_set, unit, None, 4, n)
            if unit in bad:
                values.add(result_set, unit, None, 5, bad[unit])
                values.add(result_set, unit, None, 3, n + bad[unit])
        values.add(result_set, units.roll(code_of(division)), None, 2, number(row["Enrolment"]))
    return len(divisions)


def _senators_1998() -> dict[str, list[str]]:
    """The senators elected in 1998 by state, as 'Given Surname', from the senators elected table."""
    out: dict[str, list[str]] = defaultdict(list)
    state = None
    for line in text("data/senres/tables98/text/SENATE98.TXT").splitlines():
        cells = [c.strip() for c in line.split("\t")]
        head = cells[0].rstrip("*").lower()
        if head in STATE_BY_NAME:
            state = STATE_BY_NAME[head]
            continue
        filled = [c for c in cells if c]
        if state and filled:
            match = re.match(r"\d\. (.+?)\d? \(", filled[-1])
            if match:
                out[state].append(match.group(1))
    return out


def _surname_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


ADJUSTED: dict[str, int] = {}


def senate(p: Package, year: Year, election: str, path: str, units: Units, values: Values, known: set[str]) -> int:
    parties = party_names()
    ids = base.division_ids()
    divisions = {row["Division"]: row for row in table(year, "DIVS")}

    def code_of(division: str) -> str:
        return ids[(divisions[division]["State"], division.lower())]

    candidates = {(r["State"], r["Ticket"], r["Ballot Position"]): r for r in table(year, "SCANDS")}
    totals = _by_state(year)
    names_1998 = _senators_1998() if year.year == 1998 else {}
    contests = 0
    for state in STATES:
        tickets = sorted({t for s, t, _ in candidates if s == state}, key=lambda t: (t == "UG", len(t), t))
        seats = 2 if state in ("ACT", "NT") else 6
        contest = key("contest", f"cd{year.year}", "senate", state)
        contests += 1
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE,
            district_area_id=area("au.state", state),
            magnitude=seats,
            round_number=1,
            electoral_system_id=SENATE_GVT,
            default_result_kind="counted",
        )
        p.name(contest, f"Senate, {STATE_NAMES[state]}", "en", "official")
        p.add("slug", path=f"{path}/senate-{state.lower()}", entity_id=contest, is_current=True)
        method = SENATE_METHOD
        if year.year == 1998 and state == "QLD":
            method += (
                " The Court of Disputed Returns declared Heather Hill ineligible and, on 2 July 1999, Len Harris "
                "elected in her place; the outcomes are the senators after that decision, while the votes are those "
                "counted at the election."
            )
        result_set = base._result_set(p, election, contest, method)
        options: dict[str, str] = {}
        elected: list[str] = []
        position = 0
        for ticket in tickets:
            members = sorted((int(pos), c) for (s, t, pos), c in candidates.items() if s == state and t == ticket)
            if ticket != "UG":
                position += 1
                group = members[0][1]["Group"]
                abbreviations = {c["Party"] for _, c in members}
                label = f"Group {ticket}" + (f": {parties.get(group, group)}" if group else "") + " (above the line)"
                option = options[f"{ticket}:0"] = key("option", f"cd{year.year}", "senate", state, "group", ticket)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="legend",
                    party_id=base._party_of(abbreviations.pop(), known) if len(abbreviations) == 1 else None,
                    ballot_position=position,
                    ballot_label=label,
                )
            for pos, entry in members:
                position += 1
                tag = f"{state}:{ticket}:{pos}"
                label = entry["Candidate"]
                option = options[f"{ticket}:{pos}"] = _candidate(
                    p, year, contest, tag, label, position, entry["Party"], known, True
                )
                if year.year == 1998:
                    surname, _, given = label.partition(", ")
                    won = any(
                        _surname_key(n).endswith(_surname_key(surname))
                        and _surname_key(n).startswith(_surname_key(given.split()[0] if given else ""))
                        for n in names_1998[state]
                    )
                else:
                    won = entry["Order Elected"] not in ("0", "")
                if won:
                    elected.append(option)
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
        if len(elected) != seats:
            raise ValueError(f"{year.year} {state}: {len(elected)} senators matched, expected {seats}")
        rows = [
            r
            for r in table(year, "SPPVOTE")
            if r["State"] == state and r["Polling Place"].lower() != "division summary"
        ]
        valid: Counter[str] = Counter()
        bad: dict[str, int] = {}
        summed: Counter[str] = Counter()
        adjust: dict[tuple[str, str], int] = defaultdict(int)
        for division, place, repeat, block in _blocks(rows):
            if place == "Adj":
                for r in block:
                    if r["Ticket"] == "ZZ":
                        if r["Ballot Position"] == "999":
                            adjust[(division, "INFORMAL")] += number(r["Vote"])
                    else:
                        adjust[(division, f"{r['Ticket']}:{r['Ballot Position']}")] += number(r["Vote"])
                continue
            if place in SPECIAL:
                unit = units.declaration(code_of(division), SPECIAL[place])
            else:
                unit = units.place(code_of(division), _place_id(division, place, repeat), place)
            for r in block:
                vote = number(r["Vote"])
                if r["Ticket"] == "ZZ":
                    if r["Ballot Position"] == "999":
                        bad[unit] = vote
                    continue
                option = options[f"{r['Ticket']}:{r['Ballot Position']}"]
                values.add(result_set, unit, option, 1, vote)
                valid[unit] += vote
                summed[option] += vote
        net = sum(n for (_, label), n in adjust.items() if label != "INFORMAL")
        if adjust and net:
            ADJUSTED[f"{year.year}:{state}"] = net
            p.add(
                "note",
                id=key("note", result_set, "adjustments"),
                entity_id=result_set,
                kind="caveat",
                body_md="The source's polling place table closes some divisions with adjustment rows that correct "
                f"earlier counts, by {net:+,} formal votes in all for this state or territory. They belong to no "
                "polling place, and some are negative, so they are not stored; the stored votes differ from the "
                "state's declared totals by these adjustments.",
                lang="en",
            )
        total = totals[state]
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=total["FORMAL"])
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=total["INFORMAL"])
        for unit, n in valid.items():
            values.add(result_set, unit, None, 4, n)
            if unit in bad:
                values.add(result_set, unit, None, 5, bad[unit])
                values.add(result_set, unit, None, 3, n + bad[unit])
        for division, row in sorted(divisions.items()):
            if row["State"] == state:
                values.add(result_set, units.roll(code_of(division)), None, 2, number(row["Enrolment"]))
    return contests


def election(year: Year) -> Package:
    p = Package(
        f"{SOURCE}/{year.day}",
        SOURCE,
        f"The {year.year} Australian federal election, by polling place, from the Australian Electoral "
        "Commission's election statistics",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    p.uses(raw.cached(SOURCE, ZIP))
    election_id = key("election", year.day)
    path = f"au/{year.day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="general",
        date_from=year.day,
        date_to=year.day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year.year} Australian federal election", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units = Units(p, year.day)
    values = Values()
    known = base._parties()
    house(p, year, election_id, path, units, values, known)
    senate(p, year, election_id, path, units, values, known)
    frame = values.frame()
    p.frame("result_value", frame)
    summed = frame.group_by("result_set_id", "option_id", "measure").agg(pl.col("value").sum().alias("summed"))
    declared = pl.DataFrame(p.rows["declared"]).join(
        summed, on=["result_set_id", "option_id", "measure"], how="left", nulls_equal=True
    )
    off = declared.filter(pl.col("summed").is_null() | (pl.col("summed") != pl.col("value")))
    if off.height:
        houses = {r["id"] for r in p.rows["result_set"] if r["contest_id"] in houses_of(p)}
        in_house = off.filter(pl.col("result_set_id").is_in(houses)).height
        p.accept(
            "accounting.declared",
            f"{in_house} House figures (candidate votes, formal votes, or informal votes of a division) and "
            f"{off.height - in_house} Senate state figures printed in the Australian Electoral Commission's "
            "election statistics differ from the sum of its own polling place tables, by a few votes or, for some "
            "informal counts, by more; both are stored as printed",
        )
    return p


def houses_of(p: Package) -> set[object]:
    return {r["id"] for r in p.rows["contest"] if r["body_id"] == HOUSE}
