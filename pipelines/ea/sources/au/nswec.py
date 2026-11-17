import csv
import html
import io
import re
from collections import Counter, defaultdict
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.nswec"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
PAST = "https://pastvtr.elections.nsw.gov.au"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


NSWEC = key("source", SOURCE)
NSW = area("au-nsw.state", "NSW")
ASSEMBLY = key("body", "legislative-assembly")
ASSEMBLY_SYSTEM = key("electoral_system", "legislative-assembly", "opv")
COUNCIL_SYSTEM = key("electoral_system", "council", "stv")


class StateYear(NamedTuple):
    year: int
    day: date
    folder: str
    sheet: str


STATE: dict[int, StateYear] = {
    2015: StateYear(
        2015,
        date(2015, 3, 28),
        "SGE2015/data/la/state",
        "SGE%202015%20LA%20Final%20Votes.xlsx",
    ),
    2019: StateYear(2019, date(2019, 3, 23), "SG1901/LA/state", "SGE%202019%20LA%20Final%20Votes.xlsx"),
    2023: StateYear(2023, date(2023, 3, 25), "SG2301/LA/state", "SGE%202023%20LA%20Final%20Votes.xlsx"),
}


class LocalYear(NamedTuple):
    year: int
    day: date
    combined: str


LOCAL: dict[int, LocalYear] = {
    2021: LocalYear(
        2021,
        date(2021, 12, 4),
        "browser://20261117-053307-c11a3b/nsw-lg2101-councillor-firstpreferencebyvenue-all.csv",
    ),
    2024: LocalYear(
        2024,
        date(2024, 9, 14),
        "browser://20261117-053307-c11a3b/nsw-lg2401-councillor-firstpreferencebyvenue-all (1).csv",
    ),
}


def state_url(year: int) -> str:
    config = STATE[year]
    return f"{PAST}/{config.folder}/{config.sheet}"


# Parties ---------------------------------------------------------------------------------------------------------

STATE_ALIASES = {"LAB": "ALP", "NP": "NAT", "PHON": "ON"}
NO_PARTY = {"", "IND", "Independent"}


def state_party(code: str) -> str:
    return STATE_ALIASES.get(code, code)


def local_party_code(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


ASSEMBLY_METHOD = (
    "First preference votes per voting venue, as the NSW Electoral Commission publishes them in its final votes "
    "spreadsheet for each election. Ordinary votes are reported per election-day voting centre and per early voting "
    "centre; absent, postal, iVote, provisional, enrolment, and declared-institution votes are reported once per "
    "district and are stored as one unit each. The count of preferences after the first is not stored here, and no "
    "elected member is recorded."
)
COUNCIL_METHOD = (
    "First preference votes per voting venue for the councillor election of one council, as the NSW Electoral "
    "Commission publishes them. A voter marks a group above the line or candidates below the line: each group's "
    "above-the-line votes are stored on the group's list option, and each candidate's below-the-line first "
    "preferences on the candidate's option, so the two together add up to the formal votes. The distribution of "
    "preferences and the councillors elected are not stored here."
)
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        ASSEMBLY_SYSTEM,
        date(1981, 1, 1),
        None,
        {"formula": "irv"},
        (
            "Each district elects one member to the Legislative Assembly by optional preferential voting: voters "
            "number as many boxes as they choose, and the candidate with the fewest votes is excluded and their "
            "ballots passed on until one candidate holds a majority of the ballots still in the count."
        ),
    ),
    (
        COUNCIL_SYSTEM,
        date(1990, 1, 1),
        None,
        {"formula": "stv", "quota": "droop"},
        (
            "Councillors are elected by single transferable vote in a council-wide or ward contest. Voters mark a "
            "group above the line or number candidates below the line."
        ),
    ),
]

BYE_RESULTS = "browser://20261117-053307-c11a3b/nsw-state-byelections-2014-2025-results-host-reports.html"
BYE_PAST = "browser://20261117-053307-c11a3b/nsw-state-byelections-2017-2025-pastvtr-pages.html"
BYE_DAYS = {
    "SB1401": date(2014, 10, 25),
    "SB1701": date(2017, 4, 8),
    "SB1702": date(2017, 10, 14),
    "SB1801": date(2018, 9, 8),
    "SB2101": date(2021, 5, 22),
    "SB2201": date(2022, 2, 12),
    "SB2401": date(2024, 6, 22),
    "SB2402": date(2024, 10, 19),
    "SB2501": date(2025, 3, 15),
    "SB2502": date(2025, 9, 13),
}
MONTHS = {
    m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)
}
BYE_METHOD = (
    "First preference votes per voting venue in one by-election for the Legislative Assembly, as the NSW Electoral "
    "Commission's first preference report prints them. Votes counted outside a voting venue (absent, postal, iVote, "
    "provisional, enrolment, and declared-institution votes) are reported once per district and are stored as one "
    "unit each. The distribution of preferences and the member elected are not stored here."
)


# Fetching --------------------------------------------------------------------------------------------------------


def fetch() -> None:
    for year in STATE:
        raw.fetch(SOURCE, state_url(year), suffix=".xlsx")


# Parsing ---------------------------------------------------------------------------------------------------------


class Row(NamedTuple):
    district: str
    subtype: str
    venue: str
    candidate: str
    party: str
    party_name: str
    votes: int


def state_rows(year: int) -> list[Row]:
    path = raw.cached(SOURCE, state_url(year)).path
    frame = fastexcel.read_excel(str(path)).load_sheet("Data", header_row=0).to_polars()
    out: list[Row] = []
    for r in frame.iter_rows(named=True):
        out.append(
            Row(
                str(r["District"]).strip(),
                str(r["Vote Sub Type"]).strip(),
                str(r["Venue/Declaration Name"]).strip(),
                "Informal" if r["Formal/Informal"] == "Informal" else str(r["Candidate Ballot Name"]).strip(),
                str(r["Party Acronym"] or "").strip(),
                str(r["Party Name"] or "").strip(),
                int(r["Final FP Votes"]),
            )
        )
    return out


def person_name(ballot: str) -> str:
    tokens = ballot.split()
    count = 0
    for i, token in enumerate(tokens):
        if token.isupper() and (len(token) > 1 or i == 0):
            count += 1
        else:
            break
    if count == len(tokens):
        count = 1
    surname = " ".join(tokens[:count]).title()
    surname = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), surname)
    surname = re.sub(r"\bMac([a-z])(?=[a-z]{3,})", lambda m: "Mac" + m.group(1).upper(), surname)
    return " ".join([*tokens[count:], surname]).strip()


def unit_kind(subtype: str, venue: str) -> str:
    text = f"{subtype} {venue}".lower()
    if "postal" in text:
        return "postal_district"
    if any(w in text for w in ("absent", "declared", "enrolment", "provisional", "ivote", "silent")):
        return "aggregate"
    return "standard"


class Values:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "count_round", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        for column, item in zip(self.columns, (result_set, unit, option, measure, 0, value), strict=True):
            self.columns[column].append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


class Block(NamedTuple):
    council: str
    url: str
    header: list[str]
    rows: list[list[str]]


def council_name(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


def local_blocks(year: int) -> list[Block]:
    text = raw.cached(SOURCE, LOCAL[year].combined).path.read_text(encoding="utf-8-sig")
    blocks: list[Block] = []
    current: list[str] | None = None
    url = ""
    for line in text.splitlines():
        if line.startswith("### SOURCE:"):
            if current is not None:
                blocks.append(_block(url, current))
            url = line.split(": ", 1)[1].strip()
            current = []
        elif current is not None and line.strip():
            current.append(line)
    if current is not None:
        blocks.append(_block(url, current))
    return blocks


def _block(url: str, lines: list[str]) -> Block:
    parsed = list(csv.reader(io.StringIO("\n".join(lines))))
    match = re.search(r"/LG\d+/([^/]+)/", url)
    if not match:
        raise ValueError(f"{url}: unexpected council address")
    return Block(match.group(1), url, parsed[0], parsed[1:])


def venues(block: Block) -> list[tuple[int, str]]:
    start = 2 if block.header[0] == "Candidate" else 3
    found = [
        (i, name.strip()) for i, name in enumerate(block.header) if i >= start and name.strip() and name != "Total"
    ]
    names = [n for _, n in found]
    if len(set(names)) != len(names):
        dup = [n for n, c in Counter(names).items() if c > 1]
        raise ValueError(f"{block.url}: repeated venue names {dup}")
    return found


class Local(NamedTuple):
    group: str
    name: str
    party: str
    kind: str
    votes: dict[str, int]
    total: int


def local_contest(block: Block) -> tuple[list[Local], dict[str, tuple[int, int, int]]]:
    cols = venues(block)
    names = [n for _, n in cols]
    entries: list[Local] = []
    summary: dict[str, list[int]] = {}
    group = ""
    plain = block.header[0] == "Candidate"
    for raw_row in block.rows:
        r = ["", *raw_row] if plain else raw_row
        label = r[1].strip()
        if not label or label == "UNGROUPED CANDIDATES":
            if label:
                group = ""
            continue
        votes = {n: int(raw_row[i] or 0) for i, n in cols}
        total = int(raw_row[-1] or 0) if block.header[-1] == "Total" else sum(votes.values())
        if label.startswith("Total "):
            summary[label] = [votes[n] for n in names] + [total]
            continue
        if label == "ATL Votes":
            group = r[0].strip()
            entries.append(Local(group, label, "", "group", votes, total))
        else:
            entries.append(Local(group, label, r[2].strip(), "candidate", votes, total))
    formal = summary["Total Formal Votes"]
    informal = summary["Total Informal Ballot Papers"]
    cast = summary["Total Votes / Ballot Papers"]
    totals = {n: (formal[i], informal[i], cast[i]) for i, n in enumerate([*names, ""])}
    return entries, totals


def local_parties(years: list[int]) -> dict[str, str]:
    names: dict[str, str] = {}
    for year in years:
        for block in local_blocks(year):
            for e in local_contest(block)[0]:
                if e.kind == "candidate" and e.party not in NO_PARTY:
                    names.setdefault(local_party_code(e.party), e.party)
    return names


def state_parties(years: list[int]) -> dict[str, str]:
    names: dict[str, str] = {}
    for year in sorted(years):
        for r in state_rows(year):
            if r.candidate != "Informal" and r.party not in NO_PARTY and r.party_name:
                names[state_party(r.party)] = r.party_name
    return names


# Geo -------------------------------------------------------------------------------------------------------------


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=NSWEC,
        kind="official_body",
        publisher="NSW Electoral Commission",
        title="New South Wales state and local government election results",
        url="https://pastvtr.elections.nsw.gov.au/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© State of New South Wales (NSW Electoral Commission)",
    )


def councils(years: list[int]) -> dict[str, str]:
    found: dict[str, str] = {}
    for year in years:
        for block in local_blocks(year):
            found[block.council] = council_name(block.council)
    return found


def geo(states: list[int], locals_: list[int]) -> Package:
    districts = {(y, r.district) for y in states for r in state_rows(y)}
    found = councils(locals_)
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"New South Wales, its {len({d for _, d in districts})} Legislative Assembly districts by election, and its "
        f"{len(found)} councils, as the NSW Electoral Commission names them in its election results",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    source_row(p)
    for year in states:
        p.artifact(raw.cached(SOURCE, state_url(year)), NSWEC)
    for year in locals_:
        p.artifact(raw.cached(SOURCE, LOCAL[year].combined), NSWEC)
    for store in (BYE_PAST, BYE_RESULTS):
        p.artifact(raw.cached(SOURCE, store), NSWEC)
    for code, rank, kind, noun in (
        ("au-nsw.state", 1, "administrative", "state"),
        ("au-nsw.district", 2, "electoral_district", "district"),
        ("au-nsw.venue", 3, "precinct", "voting venue"),
        ("au-nsw.council", 2, "administrative", "council"),
        ("au-nsw.council_venue", 3, "precinct", "voting venue"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRALIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NSW, scheme_code="au-nsw.state")
    p.add("area_code", area_id=NSW, code="NSW")
    p.add("area_parent", area_id=NSW, parent_id=COUNTRY)
    p.name(NSW, "New South Wales", "en", "official")
    for year in states:
        p.add(
            "boundary_set",
            id=key("boundary_set", "au-nsw.district", year),
            scheme_code="au-nsw.district",
            vintage=str(year),
            as_of=STATE[year].day,
        )
    for year, name in sorted(districts):
        id = area("au-nsw.district", f"{year}:{name}")
        p.add("area", id=id, scheme_code="au-nsw.district")
        p.add("area_code", area_id=id, code=f"{year}:{name}")
        p.add("area_parent", area_id=id, parent_id=NSW)
        p.name(id, name, "en", "official")
    for b in byes():
        id = area("au-nsw.district", f"by:{b.district}")
        p.add("area", id=id, scheme_code="au-nsw.district")
        p.add("area_code", area_id=id, code=f"by:{b.district}")
        p.add("area_parent", area_id=id, parent_id=NSW)
        p.name(id, b.district, "en", "official")
    for slug, name in sorted(found.items()):
        id = area("au-nsw.council", slug)
        p.add("area", id=id, scheme_code="au-nsw.council")
        p.add("area_code", area_id=id, code=slug)
        p.add("area_parent", area_id=id, parent_id=NSW)
        p.name(id, name, "en", "official")
    return p


# Registry --------------------------------------------------------------------------------------------------------


def registry(states: list[int], locals_: list[int]) -> Package:
    sparties = state_parties(states)
    lparties = local_parties(locals_)
    found = councils(locals_)
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        f"The New South Wales Legislative Assembly, the councils, their electoral systems, and the "
        f"{len(sparties) + len(lparties)} parties and groups on the NSW Electoral Commission's results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="lower_house")
    p.name(ASSEMBLY, "New South Wales Legislative Assembly", "en", "official")
    for slug, name in sorted(found.items()):
        id = key("body", "council", slug)
        p.add("body", id=id, polity_id=AUSTRALIA, kind="council")
        p.name(id, f"{name} Council", "en", "official")
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, name in sorted(sparties.items()):
        id = key("party", "state", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    for id, name in sorted(bye_parties().items()):
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    for code, name in sorted(lparties.items()):
        id = key("party", "local", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    return p


# Elections -------------------------------------------------------------------------------------------------------


def state_election(year: int) -> Package:
    config = STATE[year]
    day = config.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} New South Wales state election, Legislative Assembly first preferences by voting venue, from the "
        "NSW Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    p.uses(raw.cached(SOURCE, state_url(year)))
    election_id = key("election", day)
    path = f"au/nsw/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} New South Wales state election", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Values()
    by_district: dict[str, list[Row]] = defaultdict(list)
    for r in state_rows(year):
        by_district[r.district].append(r)
    for name, district_rows in sorted(by_district.items()):
        contest = key("contest", day, "assembly", name)
        district = area("au-nsw.district", f"{year}:{name}")
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=ASSEMBLY_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/assembly-{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NSWEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=ASSEMBLY_METHOD,
            lang="en",
        )
        parties_of: dict[str, str] = {}
        for r in district_rows:
            if r.candidate != "Informal":
                parties_of[r.candidate] = r.party
        options: dict[str, str] = {}
        for order, candidate in enumerate(sorted(parties_of), 1):
            id = options[candidate] = key("option", day, name, candidate)
            code = state_party(parties_of[candidate])
            pid = key("party", "state", code) if parties_of[candidate] not in NO_PARTY else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                party_id=pid,
                ballot_position=None,
                ballot_label=candidate,
            )
            person = key("person", day, name, candidate)
            p.add("person", id=person)
            p.name(person, person_name(candidate), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, name, candidate),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=candidate,
                member_of_party_id=pid,
                is_independent=parties_of[candidate] in NO_PARTY,
            )
            del order
        units: dict[tuple[str, str], str] = {}
        valid: Counter[str] = Counter()
        bad: Counter[str] = Counter()
        for r in district_rows:
            ident = (r.subtype, r.venue)
            if ident not in units:
                kind = unit_kind(r.subtype, r.venue)
                unit_id = units[ident] = key("unit", day, name, r.subtype, r.venue)
                p.add(
                    "reporting_unit",
                    id=unit_id,
                    polity_id=AUSTRALIA,
                    scheme_code="au-nsw.venue",
                    code=f"{day}:{name}:{r.subtype}:{r.venue}",
                    area_relation="within",
                    area_id=district,
                    kind=kind,
                )
                p.name(unit_id, r.venue if kind == "standard" else f"{name}: {r.venue}", "en", "official")
            unit = units[ident]
            if r.candidate == "Informal":
                bad[unit] += r.votes
            else:
                values.add(result_set, unit, options[r.candidate], 1, r.votes)
                valid[unit] += r.votes
        for unit in units.values():
            values.add(result_set, unit, None, 4, valid[unit])
            values.add(result_set, unit, None, 5, bad[unit])
            values.add(result_set, unit, None, 3, valid[unit] + bad[unit])
    p.frame("result_value", values.frame())
    return p


def local_election(year: int) -> Package:
    config = LOCAL[year]
    day = config.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} New South Wales local government elections, councillor first preferences by voting venue, from "
        "the NSW Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    p.uses(raw.cached(SOURCE, config.combined))
    election_id = key("election", day)
    path = f"au/nsw/{day}-local"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} New South Wales local government elections", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Values()
    for block in sorted(local_blocks(year), key=lambda b: b.council):
        slug = block.council
        name = council_name(slug)
        entries, totals = local_contest(block)
        contest = key("contest", day, "council", slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=key("body", "council", slug),
            district_area_id=area("au-nsw.council", slug),
            magnitude=None,
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{name} councillors", "en", "official")
        p.add("slug", path=f"{path}/council-{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NSWEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD,
            lang="en",
        )
        group_option: dict[str, str] = {}
        group_party: dict[str, set[str]] = defaultdict(set)
        for e in entries:
            if e.kind == "candidate" and e.group and e.party not in NO_PARTY:
                group_party[e.group].add(e.party)
        options: list[tuple[str, Local]] = []
        for i, e in enumerate(entries, 1):
            if e.kind == "group":
                id = group_option[e.group] = key("option", day, slug, "group", e.group)
                labels = group_party[e.group]
                pid = key("party", "local", local_party_code(next(iter(labels)))) if len(labels) == 1 else None
                p.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="list",
                    party_id=pid,
                    ballot_position=i,
                    ballot_label=f"Group {e.group}",
                )
                options.append((id, e))
                continue
            id = key("option", day, slug, "candidate", i, e.name)
            independent = e.party in NO_PARTY
            pid = None if independent else key("party", "local", local_party_code(e.party))
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                parent_option_id=group_option.get(e.group) if e.group else None,
                party_id=pid,
                ballot_position=i,
                ballot_label=e.name,
            )
            person = key("person", day, slug, i, e.name)
            p.add("person", id=person)
            p.name(person, person_name(e.name), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, slug, i, e.name),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=e.name,
                member_of_party_id=pid,
                is_independent=independent,
            )
            options.append((id, e))
        for venue, (formal, informal, cast) in totals.items():
            if venue == "":
                continue
            kind = unit_kind("", venue)
            unit = key("unit", day, slug, venue)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRALIA,
                scheme_code="au-nsw.council_venue",
                code=f"{day}:{slug}:{venue}",
                area_relation="within",
                area_id=area("au-nsw.council", slug),
                kind=kind,
            )
            p.name(unit, venue if kind == "standard" else f"{name}: {venue}", "en", "official")
            for option, e in options:
                values.add(result_set, unit, option, 1, e.votes[venue])
            values.add(result_set, unit, None, 4, formal)
            values.add(result_set, unit, None, 5, informal)
            values.add(result_set, unit, None, 3, cast)
    p.frame("result_value", values.frame())
    return p


# By-elections ----------------------------------------------------------------------------------------------------


class Bye(NamedTuple):
    code: str
    day: date
    district: str
    url: str
    store: str
    enrolled: int | None
    candidates: list[tuple[str, str, str]]
    rows: list[tuple[str, str, list[int], int, int]]
    formal: int | None
    summary: list[tuple[str, str, int]]


def _pages(store: str) -> dict[str, str]:
    text = raw.cached(SOURCE, store).path.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"<!-- SOURCE: (\S+) -->", text)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def _cells(page: str) -> list[list[str]]:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.DOTALL)
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL | re.IGNORECASE):
        out.append(
            [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.DOTALL | re.IGNORECASE)
            ]
        )
    return out


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def _bye(url: str, page: str, store: str) -> Bye | None:
    code = re.search(r"/(sb\d{4})/", url, re.IGNORECASE)
    leaf = url.rsplit("/", 1)[1].lower()
    if not code or not (
        leaf.startswith(("fp_summary", "checkcountpreferencesreport", "firstpreferencevotes", "firstpreferencesreport"))
    ):
        return None
    sb = code.group(1).upper()
    rows = _cells(page)
    head = next(i for i, r in enumerate(rows) if r and r[0].startswith(("Venue and Vote Types", "Polling Place")))
    legacy = rows[head][0] == "Polling Place"
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    when = re.search(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) (\d+) (\w+) (\d{4})", text)
    short = re.search(r"(\d\d)-([A-Z]{3})-(\d{4})", text)
    old = re.search(r"Election Day:\s*\w+,(\d+)-(\w+)-(\d{4})", text)
    if old:
        day = date(int(old.group(3)), MONTHS[old.group(2)[:3].upper()], int(old.group(1)))
    elif when:
        day = date(int(when.group(3)), list(MONTHS).index(when.group(2)[:3].upper()) + 1, int(when.group(1)))
    elif short:
        day = date(int(short.group(3)), MONTHS[short.group(2)], int(short.group(1)))
    else:
        day = BYE_DAYS[sb]
    enrolled = re.search(r"Electors Enrolled as on [\d/]+: ([\d,]+)|Number of Electors: ([\d,]+)", text)
    parts = url.split("/")
    district = parts[-3].replace("_", " ") if "Parliamentary" in url else council_name(parts[-2])
    summary: list[tuple[str, str, int]] = []
    formal: int | None = None
    start = next((i for i, r in enumerate(rows) if r and r[0] == "Candidate"), None)
    if start is not None:
        for r in rows[start + 1 : head]:
            if r and r[0] == "TOTAL FORMAL VOTES":
                formal = _int(r[2])
                break
            if len(r) >= 3 and r[0] and r[2]:
                summary.append((r[0], r[1], _int(r[2])))
    labels = rows[head][1:]
    last = labels.index("Total Formal")
    columns: list[tuple[str, str, str]] = []
    for i, label in enumerate(labels[:last]):
        if legacy:
            surname, acronym = rows[head - 1][1 + i], label
        else:
            match = re.match(r"(.+?)\s*\((\w+)\)$", label)
            surname, acronym = (match.group(1), match.group(2)) if match else (label, "")
        if summary:
            name, party, _ = summary[i]
            if not name.upper().startswith(surname.upper().replace(" ", " ")):
                raise ValueError(f"{url}: column {label!r} does not match candidate {name!r}")
        else:
            name, party = surname, ""
        columns.append((name, party, acronym))
    table: list[tuple[str, str, list[int], int, int]] = []
    section = ""
    for r in rows[head + 1 :]:
        if not r or not r[0]:
            continue
        if all(not c for c in r[1:]):
            section = r[0]
            continue
        if r[0].startswith(("Total ", "% of")):
            continue
        if not all(re.fullmatch(r"[\d,]+", c) for c in r[1 : 4 + last]):
            if not r[0].startswith(("Total", "%", "Percent", "Enrolled", "Turnout", "Number of")):
                raise ValueError(f"{url}: unexpected row {r[:3]}")
            continue
        votes = [_int(c) for c in r[1 : 1 + last]]
        table.append((section, r[0], votes, _int(r[1 + last + 1]), _int(r[1 + last])))
    return Bye(
        sb,
        day,
        district,
        url,
        store,
        _int(enrolled.group(1) or enrolled.group(2)) if enrolled else None,
        columns,
        table,
        formal,
        summary,
    )


def byes() -> list[Bye]:
    found: dict[tuple[str, str], Bye] = {}
    for store in (BYE_PAST, BYE_RESULTS):
        for url, page in _pages(store).items():
            b = _bye(url, page, store)
            if b is not None:
                found[(b.code, changeset.slug(b.district))] = b
    return sorted(found.values(), key=lambda b: (b.day, b.district))


def bye_party(name: str, acronym: str) -> tuple[str, str]:
    """The party key and display name for a by-election candidate's printed party."""
    if name and name not in NO_PARTY:
        flat = re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(the|nsw)\b", "", name.lower()))
        for code, label in state_parties(sorted(STATE)).items():
            if re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(the|nsw)\b", "", label.lower())) == flat:
                return key("party", "state", code), label
        return key("party", "bye", flat), name
    if acronym and acronym not in NO_PARTY:
        code = state_party(acronym)
        if code in state_parties(sorted(STATE)):
            return key("party", "state", code), ""
        return key("party", "bye", code.lower()), code
    return "", ""


def bye_parties() -> dict[str, str]:
    names: dict[str, str] = {}
    for b in byes():
        for name, party, acronym in b.candidates:
            pid, label = bye_party(party, acronym)
            if pid and label:
                names.setdefault(pid, label)
    state = {key("party", "state", c) for c in state_parties(sorted(STATE))}
    return {pid: n for pid, n in names.items() if pid not in state}


def bye_election(day: date, items: list[Bye]) -> Package:
    p = Package(
        f"{SOURCE}/by-{day}",
        SOURCE,
        f"The New South Wales Legislative Assembly by-election{'s' if len(items) > 1 else ''} of {day}, first "
        "preferences by voting venue, from the NSW Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    for store in sorted({b.store for b in items}):
        p.uses(raw.cached(SOURCE, store))
    election_id = key("election", "by", day)
    path = f"au/nsw/{day}-by-election"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="by_election",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(
        election_id,
        f"{day.year} New South Wales state by-election{'s' if len(items) > 1 else ''} ({day.day} {day:%B})",
        "en",
        "official",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Values()
    for b in items:
        name = b.district
        contest = key("contest", "by", day, name)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=area("au-nsw.district", f"by:{name}"),
            magnitude=1,
            round_number=1,
            electoral_system_id=ASSEMBLY_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/assembly-{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NSWEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=BYE_METHOD,
            lang="en",
        )
        options: list[str] = []
        for i, (cand, party, acronym) in enumerate(b.candidates, 1):
            id = key("option", "by", day, name, cand)
            options.append(id)
            pid, _ = bye_party(party, acronym)
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                party_id=pid or None,
                ballot_position=i,
                ballot_label=cand,
            )
            person = key("person", "by", day, name, cand)
            p.add("person", id=person)
            p.name(person, person_name(cand) if cand.split()[0].isupper() else cand, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", "by", day, name, cand),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=cand,
                member_of_party_id=pid or None,
                is_independent=not pid,
            )
        for i, (_, _, votes) in enumerate(b.summary):
            p.add("declared", result_set_id=result_set, option_id=options[i], measure=1, value=votes)
        if b.formal is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=b.formal)
        seen: Counter[tuple[str, str]] = Counter()
        for section, venue, votes, informal, formal in b.rows:
            seen[(section, venue)] += 1
            if seen[(section, venue)] > 1:
                raise ValueError(f"{b.url}: venue {venue!r} repeats in {section!r}")
            unit = key("unit", "by", day, name, section, venue)
            kind = unit_kind(section if "eclaration" in section else "", venue)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRALIA,
                scheme_code="au-nsw.venue",
                code=f"by:{day}:{name}:{section}:{venue}",
                area_relation="within",
                area_id=area("au-nsw.district", f"by:{name}"),
                kind=kind,
            )
            p.name(unit, venue if kind == "standard" else f"{name}: {venue}", "en", "official")
            for option, count in zip(options, votes, strict=True):
                values.add(result_set, unit, option, 1, count)
            values.add(result_set, unit, None, 4, formal)
            values.add(result_set, unit, None, 5, informal)
            values.add(result_set, unit, None, 3, formal + informal)
        if b.enrolled is not None:
            roll = key("unit", "by", day, name, "roll")
            p.add(
                "reporting_unit",
                id=roll,
                polity_id=AUSTRALIA,
                scheme_code="au-nsw.venue",
                code=f"by:{day}:{name}:ROLL",
                area_relation="within",
                area_id=area("au-nsw.district", f"by:{name}"),
                kind="aggregate",
            )
            p.name(roll, f"{name}: electoral roll", "en", "official")
            values.add(result_set, roll, None, 2, b.enrolled)
    p.frame("result_value", values.frame())
    return p


def bye_groups() -> dict[date, list[Bye]]:
    groups: dict[date, list[Bye]] = defaultdict(list)
    for b in byes():
        groups[b.day].append(b)
    return dict(groups)


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{c.day}" for c in STATE.values()),
        *(f"{SOURCE}/{c.day}" for c in LOCAL.values()),
        *(f"{SOURCE}/by-{day}" for day in bye_groups()),
    ]


def parse() -> None:
    states, locals_ = sorted(STATE), sorted(LOCAL)
    geo(states, locals_).write()
    registry(states, locals_).write()
    for year in states:
        state_election(year).write()
    for year in locals_:
        local_election(year).write()
    for day, items in bye_groups().items():
        bye_election(day, items).write()


def emit() -> None:
    package.run("load", packages())
