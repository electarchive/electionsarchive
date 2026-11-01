import base64
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.wikipedia_primaries import API
from ea.store import DATA, connect

SOURCE = "us.conventions"
VERSION = "1"
VOTES, DELEGATION = 1, 30
PLACES = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia (U.S. state)": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    "Washington, D.C.": "DC",
    "Puerto Rico": "PR",
    "Guam": "GU",
    "American Samoa": "AS",
    "Virgin Islands, U.S.": "VI",
    "Northern Marianas": "MP",
}
CONVENTIONS = [
    {
        "key": "2016-02-01:president:republican",
        "party": "republican",
        "title": "2016 Republican National Convention",
        "table": "Republican presidential nomination ballot",
        "days": (date(2016, 7, 18), date(2016, 7, 21)),
        "ballots": [date(2016, 7, 19)],
        "required": 1237,
        "nominee": "Trump",
        "city": "Cleveland",
    },
    {
        "key": "2016-02-01:president:democratic",
        "party": "democratic",
        "title": "2016 Democratic National Convention",
        "table": "Democratic presidential nomination ballot",
        "days": (date(2016, 7, 25), date(2016, 7, 28)),
        "ballots": [date(2016, 7, 26)],
        "required": 2383,
        "nominee": "Clinton",
        "city": "Philadelphia",
    },
]
ABROAD = "DA"
METHOD = (
    "The roll call of the states on the presidential nomination, one ballot per contest, with each delegation's "
    "votes as the convention secretary recorded them. The party has published no official report of the proceedings "
    "for this convention, so the votes are transcribed from the roll-call table in the English Wikipedia's article on "
    "the convention, pinned to the revision current when it was fetched, until an official tally is found. Each "
    "delegation's total is the delegates it held, abstentions included; abstentions are that total less the votes "
    "recorded for candidates."
)
ABROAD_NOTE = (
    "Democrats Abroad is stored as an unlocated delegation, apart from the states and territories. Eight of its "
    "superdelegates cast half votes, so its 21 delegates cast 17 votes, and its total here is the 17 votes. One "
    "superdelegate position assigned to no state did not vote and is not counted in any delegation."
)
REPORTED = (
    "The delegations of Alaska, the District of Columbia, and Utah announced votes for candidates who had left the "
    "race (Alaska: 11 Trump, 12 Cruz, and 5 Rubio; the District of Columbia: 10 Rubio and 9 Kasich; Utah: 40 Cruz). "
    "Under the party's rules those delegates were bound to the remaining candidate, and the secretary recorded every "
    "one of them for Trump, as stored here."
)


def page(title: str) -> str:
    return API + urlencode(
        {"action": "parse", "page": title, "prop": "wikitext|revid", "format": "json", "formatversion": 2}
    )


LEAF = "https://archive.org/download/{item}/page/n{leaf}.jpg"
RNC = "sim_republican-national-convention-proceedings_{}"
TABLES = [
    {"table": "1968:republican:first", "item": RNC.format("august-5-8-1968"), "leaves": (409, 410)},
    {"table": "1968:republican:final", "item": RNC.format("august-5-8-1968"), "leaves": (419, 420)},
    {"table": "1972:republican", "item": RNC.format("august-21-23-1972_30"), "leaves": (361, 362)},
    {"table": "1976:republican", "item": RNC.format("august-16-19-1976_31"), "leaves": (451, 452)},
    {"table": "1980:republican", "item": RNC.format("july-14-17-1980"), "leaves": (479, 480)},
    {
        "table": "1984:republican",
        "item": RNC.format("august-20-23-1984_33"),
        "leaves": (444, 445),
        "columns": ("votes", "Reagan", None, "Abstention", None, None, None),
    },
]
READS = {"a": "low", "b": "medium"}
PROMPT = "1"
ROLL = (
    "This page is from the official report of the proceedings of a national party convention. Read the tabulation "
    "of the ballot for president: one row per state or territory, in the order printed, including any totals row "
    "(labeled as printed, such as 'Totals'). The header lists only the numeric columns, left to right (the column "
    "of votes or delegates each state holds, then each candidate, abstention, or other column, labels as printed); "
    "never the state column. Each row's values hold one string per header column, in the same order, empty where "
    "the cell is blank. Read the rows and columns that continue the table from a previous page in the same way. If "
    "the page holds no such table, return an empty header and no rows."
)
ABBREVIATIONS = {
    "ala": "AL", "ariz": "AZ", "ark": "AR", "calif": "CA", "cal": "CA", "colo": "CO", "conn": "CT", "del": "DE",
    "dc": "DC", "fla": "FL", "ga": "GA", "ill": "IL", "ind": "IN", "kans": "KS", "kan": "KS", "ky": "KY", "la": "LA",
    "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN", "miss": "MS", "mo": "MO", "mont": "MT", "nebr": "NE",
    "neb": "NE", "nev": "NV", "nh": "NH", "nj": "NJ", "nmex": "NM", "nm": "NM", "ny": "NY", "nc": "NC", "nd": "ND",
    "ndak": "ND", "okla": "OK", "ore": "OR", "oreg": "OR", "pa": "PA", "pr": "PR", "ri": "RI", "sc": "SC",
    "sd": "SD", "sdak": "SD", "tenn": "TN", "tex": "TX", "vt": "VT", "va": "VA", "vi": "VI", "wash": "WA",
    "wva": "WV", "wis": "WI", "wisc": "WI", "wyo": "WY", "virginislands": "VI", "puertorico": "PR",
    "districtofcolumbia": "DC", "canalzone": "CZ",
}  # fmt: skip
NAMES = {re.sub(r"[^a-z]", "", re.sub(r" \(.*\)|, U\.S\.", "", name).lower()): code for name, code in PLACES.items()}
HALF = re.compile(r"\s*(?:½|1/2|'/2|\.5)$")


def fetch() -> None:
    for convention in CONVENTIONS:
        raw.fetch(SOURCE, page(convention["title"]), suffix=".json")
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch, scans

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for spec in TABLES:
        for leaf in spec["leaves"]:
            image = raw.fetch(SOURCE, LEAF.format(item=spec["item"], leaf=leaf), suffix=".jpg")
            data = base64.b64encode(image.path.read_bytes()).decode()
            for effort in READS.values():
                wanted[read_key(effort, image.sha256)] = (
                    MessageCreateParamsNonStreaming(
                        model="claude-haiku-5-5",
                        max_tokens=16000,
                        system=scans.SYSTEM,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
                                    },
                                    {"type": "text", "text": ROLL},
                                ],
                            }
                        ],
                        output_config={"effort": effort, "format": {"type": "json_schema", "schema": scans.SCHEMA}},
                    ),
                    (3000 * price_in + 4000 * price_out) / 1e6,
                )
    print(batch.ensure(SOURCE, "rollcalls", wanted)[1])


def read_key(effort: str, digest: str) -> str:
    return f"anthropic-batch:claude-haiku-5-5/{PROMPT}/{effort}/{digest}"


def place(label: str) -> str | None:
    plain = re.sub(r"[^a-z]", "", label.lower().split("(")[0])
    return NAMES.get(plain) or ABBREVIATIONS.get(plain)


def number(cell: str) -> float:
    text = cell.strip().replace(",", "")
    half = HALF.search(text)
    text = text[: half.start()] if half else text
    if text in {"", "-", "—", "–", ".", "·", "*"} and not half:
        return 0.0
    return (float(text) if text else 0.0) + (0.5 if half else 0.0)


def table(spec: dict[str, object], effort: str) -> tuple[list[dict[str, object]], dict[str, float], list[str]]:
    cells: list[dict[str, object]] = []
    totals: dict[str, float] = {}
    problems: list[str] = []
    for leaf in spec["leaves"]:
        image = raw.cached(SOURCE, LEAF.format(item=spec["item"], leaf=leaf))
        try:
            response = json.loads(raw.cached(SOURCE, read_key(effort, image.sha256)).path.read_text())
        except LookupError:
            problems.append(f"leaf {leaf}: not read")
            continue
        text = next((block["text"] for block in response["content"] if block["type"] == "text"), "{}")
        read = json.loads(text)
        header = read["header"]
        roles = spec.get("columns") or tuple(
            "votes" if re.search(r"vote|delegate", column["label"], re.IGNORECASE) else column["label"].strip()
            for column in header
        )
        if len(roles) != len(header):
            problems.append(f"leaf {leaf}: {len(header)} columns for {len(roles)} roles")
            continue
        for row in read["rows"]:
            if len(row["values"]) != len(header):
                problems.append(f"leaf {leaf}: {row['label']} has {len(row['values'])} cells")
                continue
            code = place(row["label"])
            if code is None and not re.match(r"^\s*totals?\b", row["label"], re.IGNORECASE):
                problems.append(f"leaf {leaf}: unknown row {row['label']!r}")
                continue
            for role, column, value in zip(roles, header, row["values"], strict=True):
                if role is None:
                    continue
                try:
                    figure = number(value)
                except ValueError:
                    problems.append(f"leaf {leaf}: {row['label']} {role} reads {value!r}")
                    continue
                if code is None:
                    totals[role] = figure
                    continue
                cells.append(
                    {
                        "table": spec["table"],
                        "state": code,
                        "column": role,
                        "value": figure,
                        "leaf": leaf,
                        "sha256": image.sha256,
                        "box": [column["x0"] / 1000, row["y0"] / 1000, column["x1"] / 1000, row["y1"] / 1000],
                    }
                )
    return cells, totals, problems


def parse() -> None:
    folder = DATA / "parsed" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    frames, report = [], []
    for spec in TABLES:
        reads = {effort: table(spec, effort) for effort in READS.values()}
        (first, printed, problems), (second, _, others) = reads["low"], reads["medium"]
        values = {(cell["state"], cell["column"]): cell["value"] for cell in first}
        other = {(cell["state"], cell["column"]): cell["value"] for cell in second}
        disagree = sorted(key for key in values.keys() | other.keys() if values.get(key) != other.get(key))
        failures = [*problems, *others, *(f"reads disagree on {state} {column}" for state, column in disagree)]
        held = {state for state, _ in values}
        for state in sorted(held):
            votes = values.get((state, "votes"), 0)
            cast = sum(value for (code, column), value in values.items() if code == state and column != "votes")
            if votes != cast:
                failures.append(f"{state}: {cast:g} cast of {votes:g} held")
        for column, total in printed.items():
            summed = sum(value for (_, name), value in values.items() if name == column)
            if summed != total:
                failures.append(f"column {column}: states sum to {summed:g}, printed total {total:g}")
        report.append(f"## {spec['table']}\n\n{len(held)} delegations, {len(values)} cells, printed totals {printed}")
        report.extend(f"- {failure}" for failure in failures)
        report.append("")
        if not failures:
            frames.append(pl.DataFrame(first))
    (folder / "rollcalls.md").write_text("\n".join(report))
    if frames:
        pl.concat(frames).write_parquet(folder / "rollcalls.parquet")
    print("\n".join(report))


def roll_call(text: str, caption: str) -> tuple[list[str], dict[str, list[int]]]:
    start = text.index(caption)
    table = text[start : text.index("\n|}", start)]
    rows = table.split("\n|-\n")
    candidates = [re.search(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]\s*$", line) for line in rows[0].split("\n")[2:]]
    labels = [match.group(1) if match else "Abstain" for match in candidates]
    votes: dict[str, list[int]] = {}
    for row in rows[1:]:
        place = re.search(r"\{\{flag(?:country)?\|([^}]*)\}\}|\[\[(Democrats Abroad)\]\]", row)
        if place is None:
            continue
        code = ABROAD if place.group(2) else PLACES[place.group(1)]
        cells = re.sub(
            r"\{\{refn\|.*?\|group=[^}]*\}\}|<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", row, flags=re.DOTALL
        ).split("\n|")[1:]
        if len(cells) != len(labels):
            raise ValueError(f"{code}: {len(cells)} cells for {len(labels)} columns")
        values = []
        for cell in cells:
            recorded = cell.split("|")[-1].split("<br>")[0].strip()
            values.append(0 if recorded in {"·", "–", ""} else int(recorded.replace(",", "")))
        votes[code] = values
    return labels, votes


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The roll call of the states at the 2016 national conventions",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        us = cs.polity("US")
        nation = cs.area("us.nation", "US")
        units = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        system = cs.key("us.electoral_system", "national-convention:majority-of-delegates")
        if system in cs.minted:
            cs.add(
                "electoral_system",
                id=system,
                polity_id=us,
                rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
                rules_version=1,
            )
            name(system, "Majority of the convention's delegates, with repeated ballots")
        values = []
        for convention in CONVENTIONS:
            key, party, title = convention["key"], convention["party"], convention["title"]
            root = conn.execute(
                """select c.id, c.election_id, c.office_id, c.party_scope_id
                   from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchone()
            if root is None:
                raise ValueError(f"{key} is not held")
            _, umbrella, office, scope = root
            people = dict(
                conn.execute(
                    """select split_part(n.text, ' ', -1), coalesce(m.into_id, cy.person_id)
                       from contest c join contest_option o on o.contest_id = c.id
                       join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                       left join entity_merge m on m.entity_id = cy.person_id
                       join name n on n.entity_id = coalesce(m.into_id, cy.person_id) and n.is_preferred
                       where c.allocation_parent_id = %s
                       group by 1, 2 order by count(*)""",
                    [root[0]],
                ).fetchall()
            )
            artifact = raw.cached(SOURCE, page(title))
            parsed = json.loads(artifact.path.read_text())["parse"]
            labels, table = roll_call(parsed["wikitext"], convention["table"])
            expected = set(units) | ({ABROAD} if ABROAD in table else set())
            if set(table) != expected:
                raise ValueError(f"{title}: delegations {sorted(expected ^ set(table))} do not match the states")
            if ABROAD in table:
                units[ABROAD] = cs.unit("us.state", ABROAD)
                if units[ABROAD] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[ABROAD],
                        polity_id=us,
                        scheme_code="us.state",
                        code=ABROAD,
                        area_relation="unlocated",
                        kind="abroad_mission",
                    )
                    name(units[ABROAD], "Democrats Abroad")
            source = cs.source("Wikipedia", f"{title}, revision {parsed['revid']}")
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="Wikipedia",
                title=f"{title}, revision {parsed['revid']}",
                url=f"https://en.wikipedia.org/w/index.php?title={title.replace(' ', '_')}&oldid={parsed['revid']}",
                license="CC BY-SA 4.0",
            )
            cs.artifact(artifact, source)
            first, last = convention["days"]
            election = cs.key("us.election", f"{first}:{party}-national-convention")
            cs.add(
                "election",
                id=election,
                polity_id=us,
                parent_id=umbrella,
                kind="indirect",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(election, title)
            path = f"us/{first}-{party}-national-convention"
            cs.add("slug", path=path, entity_id=election)
            previous = None
            for number, day in enumerate(convention["ballots"], 1):
                contest = cs.key("us.contest", f"{key}:convention:{number}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    party_scope_id=scope,
                    district_area_id=nation,
                    magnitude=1,
                    round_number=number,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    rules_override={"required": convention["required"]},
                    default_result_kind="counted",
                )
                name(contest, f"Presidential nomination, ballot {number}")
                cs.add("slug", path=f"{path}/ballot-{number}", entity_id=contest)
                previous = contest
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:convention:{number}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                    as_of=datetime.combine(day, datetime.min.time(), UTC),
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=METHOD + f" The convention met in {convention['city']} and voted on {day:%B %-d, %Y}; "
                    f"{convention['required']:,} votes, a majority of the delegates, were needed to nominate.",
                )
                if key == "2016-02-01:president:republican":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=REPORTED)
                else:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=ABROAD_NOTE)
                ballot_options = {}
                for label in labels[:-1]:
                    surname = label.split()[-1]
                    option = cs.key("us.option", f"{key}:convention:{number}:{surname.lower()}")
                    ballot_options[label] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", f"{party}-party"),
                        ballot_label=label,
                    )
                    if surname not in people:
                        raise ValueError(f"{label} has no candidacy in the {key} state contests")
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:convention:{number}:{surname.lower()}:head"),
                        person_id=people[surname],
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                    )
                for postal, row in table.items():
                    unit = str(units[postal])
                    values.append((str(result_set), unit, None, DELEGATION, sum(row)))
                    for label, value in zip(labels[:-1], row, strict=False):
                        if value:
                            values.append((str(result_set), unit, str(ballot_options[label]), VOTES, value))
                if number == len(convention["ballots"]):
                    winner = next(label for label in labels if label.endswith(convention["nominee"]))
                    cs.add("outcome", result_set_id=result_set, option_id=ballot_options[winner], kind="won")
        cs.frame(
            "result_value",
            pl.DataFrame(
                [
                    {
                        "result_set_id": rs,
                        "unit_id": unit,
                        "option_id": option,
                        "channel": 0,
                        "measure": measure,
                        "count_round": 0,
                        "value": value,
                        "flags": 0,
                    }
                    for rs, unit, option, measure, value in values
                ],
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
