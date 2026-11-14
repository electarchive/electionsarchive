import difflib
import re
import subprocess
import zipfile
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import fec
from ea.store import DATA, connect

SOURCE = "us.fec_pres_books"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (1992, 1996, 2000)
STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "PR": "Puerto Rico",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
NAMED = {name.upper(): code for code, name in STATES.items()}
HEADINGS = {
    "DEMOCRATIC": "D",
    "DEMOCRATIC-FARM-LABOR": "D",
    "INDEPENDENT-REPUBLICAN": "R",
    "REPUBLICAN": "R",
    "LIBERTARIAN": "LBT",
    "AMERICAN INDEPENDENT": "AIP",
    "PEACE & FREEDOM PARTY": "PFP",
    "PEACE AND FREEDOM": "PFP",
    "NEW ALLIANCE": "NA",
    "POPULIST": "POP",
    "TAXPAYERS": "TX",
    "U.S. TAXPAYERS": "TX",
    "INDEPENDENT": "I",
}
PARTIES = {
    "D": ("Democratic", "democratic-party"),
    "R": ("Republican", "republican-party"),
    "LBT": ("Libertarian", "libertarian-party"),
    "GRN": ("Green", "green-party"),
    "REF": ("Reform", "reform-party"),
    "AIP": ("American Independent", "american-independent-party"),
    "NL": ("Natural Law", "natural-law-party"),
    "PFP": ("Peace and Freedom", "CA:peace-and-freedom-party"),
    "CON": ("Constitution", "constitution-party"),
    "IAP": ("Independent American", "UT:independent-american-party"),
    "LU": ("Liberty Union", "VT:liberty-union-party"),
}
IOWA = {1992: date(1992, 2, 10), 1996: date(1996, 2, 12), 2000: date(2000, 1, 24)}
SKIPPED = {(2000, "NY", "R")}
CAVEATS = {
    (1996, "WA"): (
        "Washington also offered an unaffiliated ballot, whose 444,619 votes the FEC lists separately. They are not "
        "included here."
    ),
    (2000, "WA"): (
        "Washington also offered an unaffiliated ballot, whose 521,218 votes the FEC lists separately. They are not "
        "included here."
    ),
    (2000, "CA"): (
        "California's 2000 primary was a blanket primary. These are the votes of the party's own registered voters, "
        "which chose its delegates; the votes that independents and members of other parties cast for its candidates "
        "are listed separately by the FEC and are not included here."
    ),
}
FIXES = {
    "D~~Bob": "Dole, Bob",
    "Gm~Al": "Gore, Al",
    "Alexander. Lamar": "Alexander, Lamar",
    "McCain John S.": "McCain, John S.",
    "Clinton": "Clinton, Bill",
    "N one of the Names Shown": "None of the Names Shown",
    "one of the Nam es Shown": "None of the Names Shown",
    "one of the Names Shown": "None of the Names Shown",
    "o Preference": "No Preference",
    "Noncomrnitted": "Noncommitted",
    'Uncommitted"': "Uncommitted",
    "Gore, A I": "Gore, Al",
    "Gore, A l": "Gore, Al",
    "M cCain, John": "McCain, John",
    "Hage lin, John": "Hagelin, John",
    "Kove l, Joel": "Kovel, Joel",
    "LaRouc he, Lyndon H., Jr.": "LaRouche, Lyndon H., Jr.",
    "Harkin, Torn": "Harkin, Tom",
    "Fomwalt, Russell J.": "Fornwalt, Russell J.",
    "Kalernkariam, Timothy": "Kalemkarian, Timothy",
}
SAME = {
    "Stassen, Harold A.": "Stassen, Harold E.",
    'Le Tulle, Mary "France"': 'LeTulle, Mary "France"',
    "Wilder, L. Douglas": "Wilder, Lawrence Douglas",
    "Forbes, Malcolm S., Jr.": "Forbes, Steve",
}
PINNED = {r"^Brown, (Edmund G\.|Jerry)": UUID("6c11a949-17a3-55b6-827d-35e8b82d2fc3")}
CODES = {"RBF": "REF", "W{LBT)": "W(LBT)"}
DIGITS = str.maketrans("IlJ|!iOoSsBZ", "111111005582")
MONTH = r"(January|February|March|April|May|June|July|August|September)"


def number(text: str) -> int | None:
    core = re.sub(r"[\s,.;:'’`]", "", text).translate(DIGITS)
    return int(core) if core.isdigit() else None


def share(text: str) -> float | None:
    core = re.sub(r"\s", "", text).translate(DIGITS)
    return float(core) if re.fullmatch(r"\d*\.?\d+", core) else None


def section(year: int) -> list[str]:
    book = raw.cached(fec.SOURCE, fec.DOCUMENTS + fec.BOOKS[year])
    text = subprocess.run(
        ["pdftotext", "-layout", str(book.path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    starts = [index for index, line in enumerate(text) if "OFFICIAL PRIMARY ELECTION RESULTS" in line]
    start = next(index for index in starts if any("PRESIDENT" in line.upper() for line in text[index : index + 30]))
    end = next(
        index
        for index in range(start + 1, len(text))
        if re.search(r"^\s*(OFFICIAL ELECTION RESULTS|A GUIDE TO PARTY LABELS|GUIDE TO PARTY)", text[index])
    )
    return text[start:end]


def state_of(line: str) -> str | None:
    text = re.sub(r"\(.*", "", line).strip(" .,:;|-'_~*\"").upper()
    if not re.fullmatch(r"[A-Z][A-Z .]{3,}", text) or "CANDIDATE" in text or "TOTAL" in text:
        return None
    found = difflib.get_close_matches(text, list(NAMED), n=1, cutoff=0.8)
    return NAMED[found[0]] if found else None


def day_of(line: str, year: int) -> date | None:
    found = re.search(rf"PRIMARY\s*\S?LECTION\s*[:;]?\s*{MONTH}\s+([\dIl ]+?)\s*,", line, re.IGNORECASE)
    if found is None:
        return None
    return datetime.strptime(f"{found[1].title()} {number(found[2])} {year}", "%B %d %Y").date()


def parse() -> None:
    rows: list[tuple[int, str, date, str, int, str, int, float | None, int, str, bool]] = []
    problems: list[str] = []
    for year in YEARS:
        state: str | None = None
        day: date | None = None
        party: str | None = None
        block = 0
        lines: list[tuple[str, str, int | None, float | None, str]] = []

        def close(total: int | None, label: str) -> None:
            nonlocal block, lines
            if not lines:
                return
            if state is None or day is None:
                problems.append(f"{year}: a block with no state or date before {label}")
                lines = []
                return
            block += 1
            counted = [votes for *_, votes, _, _ in lines]
            if total is not None and None not in counted and sum(v or 0 for v in counted) != total:
                suspects = [
                    index
                    for index, (_, _, votes, percent, _) in enumerate(lines)
                    if votes is None or percent is None or abs(votes - percent * total / 100) > 0.00006 * total + 1
                ]
                if len(suspects) == 1:
                    index = suspects[0]
                    repaired = total - sum(v or 0 for i, (*_, v, _, _) in enumerate(lines) if i != index)
                    _, _, _, percent, _ = lines[index]
                    if percent is not None and abs(repaired - percent * total / 100) <= 0.00006 * total + 1:
                        lines[index] = (*lines[index][:2], repaired, percent, lines[index][4] + " [repaired]")
            counted = [votes for *_, votes, _, _ in lines]
            if total is None or None in counted or sum(v or 0 for v in counted) != total:
                problems.append(
                    f"{year} {state} {day} {lines[0][1]} ({label}): lines {counted} sum "
                    f"{sum(v or 0 for v in counted)}, printed {total}"
                )
            heading = re.sub(r"\s+", " ", label.split(":")[0]).strip()
            rows.extend(
                (
                    year,
                    state,
                    day,
                    code,
                    block,
                    name,
                    votes if votes is not None else -1,
                    percent,
                    total or -1,
                    heading,
                    source.endswith("[repaired]"),
                )
                for name, code, votes, percent, source in lines
            )
            lines = []

        for line in section(year):
            text = re.sub(r"^[^\w(]+", "", line.strip())
            if not text or re.fullmatch(r"-?\s*\d+\s*-?", text) or set(text) <= set("=-_.·,…"):
                continue
            if (found := state_of(text)) is not None:
                if found == state:
                    continue
                close(None, "state")
                state, day, party = found, None, None
                continue
            if state is None:
                continue
            text = re.sub(r"^T\s?o\s?t\s?a\s?l\b", "Total", text)
            if (when := day_of(text, year)) is not None:
                close(None, "date")
                day = when
                continue
            heading = re.match(r"^([A-Z.&' -]+?)\s+(PARTY\s+)?CANDIDATES", text)
            if heading:
                code = HEADINGS.get(heading[1].strip(), heading[1].strip())
                if code == party and lines:
                    continue
                close(None, "heading")
                party = code
                continue
            if re.match(r"(?i)^total", text):
                total = number(re.split(r":|\s{2,}", text)[-1]) if ":" in text or "  " in text else None
                if re.search(r"(?i)state (votes|primary|delegate)|all parties|all voters", text) and not re.search(
                    r"(?i)party votes", text
                ):
                    close(None, text)
                    continue
                close(total, text)
                continue
            cells = re.split(r"\s{2,}", text)
            if text.upper().startswith("CANDIDATE") or (len(cells) > 1 and cells[1] == "w"):
                continue
            if len(cells) < 2 or not re.search(r"[A-Za-z]", cells[0]) or not re.search(r"\d", " ".join(cells[1:])):
                continue
            if year == 1992:
                if party is None:
                    problems.append(f"{year} {state}: a line before any party heading {text!r}")
                    continue
                code, rest = party, cells[1:]
            else:
                if len(cells) < 3:
                    problems.append(f"{year} {state}: unread line {text!r}")
                    continue
                code, rest = CODES.get(cells[1], cells[1]), cells[2:]
            votes = number(rest[0])
            percent = share(rest[1]) if len(rest) > 1 else None
            if lines and lines[-1][1] != code and not code.startswith(("W(", "UN(")) and year != 1992:
                problems.append(f"{year} {state}: party changes from {lines[-1][1]} to {code} without a total")
            lines.append((cells[0].strip(), code, votes, percent, text))
        close(None, "end")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "state": pl.String,
            "day": pl.Date,
            "code": pl.String,
            "block": pl.Int32,
            "name": pl.String,
            "votes": pl.Int64,
            "percent": pl.Float64,
            "total": pl.Int64,
            "label": pl.String,
            "repaired": pl.Boolean,
        },
        orient="row",
    )
    frame.write_parquet(PARSED / "lines.parquet")
    for problem in problems:
        print(problem)
    print(f"{frame.height} lines, {frame.select('year', 'block').n_unique()} blocks, {len(problems)} problems")


def emit() -> Path:
    frame = (
        pl.read_parquet(PARSED / "lines.parquet")
        .with_columns(
            code=pl.col("code").str.replace_all(r"[\s}]", "").str.replace(r"\($", ")"),
            name=pl.col("name").map_elements(clean, return_dtype=pl.String),
        )
        .with_columns(
            party=pl.col("code")
            .str.extract(r"^(?:W\()?([A-Z& ]+)\)?$")
            .replace({"PEACE & FREEDOM": "PFP", "PEACE&FREEDOM": "PFP"}),
            write_in=pl.col("code").str.starts_with("W("),
        )
    )
    blocks = frame.group_by("year", "block").agg(
        pl.col("party").filter(~pl.col("write_in")).unique().alias("parties"), pl.col("label").first()
    )
    kept = blocks.filter(
        (pl.col("parties").list.len() == 1)
        & pl.col("parties").list.first().is_in(list(PARTIES))
        & ~pl.col("label").str.contains("Cross|Unaffiliated")
    )
    dropped = blocks.join(kept, on=["year", "block"], how="anti")
    for year, block, parties, label in dropped.sort("year", "block").iter_rows():
        print(f"{year} block {block}: {parties} ({label}) not loaded")
    frame = frame.join(kept.select("year", "block"), on=["year", "block"])
    frame = frame.join(
        frame.filter(~pl.col("write_in")).group_by("year", "block").agg(pl.col("party").first().alias("owner")),
        on=["year", "block"],
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential primaries of 1992, 1996, and 2000 by state from the FEC's Federal Elections books",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[int, UUID] = {}
        for year in YEARS:
            sources[year] = cs.source(fec.PUBLISHER, f"Federal Elections {2000 if year == 2000 else year % 100}")
            if sources[year] in cs.minted:
                raise ValueError(f"Federal Elections {year} is not a source yet")
            cs.artifact(raw.cached(fec.SOURCE, fec.DOCUMENTS + fec.BOOKS[year]), sources[year])
        keys = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.contest'")}
        elections = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        slugs = dict(conn.execute("select path, entity_id from slug").fetchall())
        states: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        pools = [
            [
                (person, words(text))
                for person, text in conn.execute(
                    """select distinct cy.person_id, n.text from candidacy cy
                       join contest_option o on o.id = cy.option_id
                       join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                            and x.value ~ ':president(:|$)'
                       join name n on n.entity_id = cy.person_id and n.is_preferred"""
                ).fetchall()
            ],
            [
                (person, words(text))
                for person, text in conn.execute(
                    """select n.entity_id, n.text from name n
                       where n.is_preferred and n.entity_id in (
                           select entity_id from external_id where scheme = 'bioguide'
                           union
                           select cy.person_id from candidacy cy
                           join contest_option o on o.id = cy.option_id
                           join contest c on c.id = o.contest_id
                           join office f on f.id = c.office_id and f.kind = 'governor'
                           join election e on e.id = c.election_id and e.date_from >= '1950-01-01'
                           join polity p on p.id = e.polity_id and p.iso_code like 'US-%%')"""
                ).fetchall()
            ],
        ]
        fec_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'fec.candidate'")}
        roster: dict[int, list[tuple[str, list[str]]]] = {}
        for year in YEARS:
            with zipfile.ZipFile(raw.cached(fec.SOURCE, fec.MASTERS[year]).path) as archive:
                for record in archive.read("cn.txt").decode("latin-1").splitlines():
                    fields = record.split("|")
                    if fields[5] == "P":
                        surname, _, given = fields[1].partition(",")
                        roster.setdefault(year, []).append((fields[0], [*words(given), *words(surname)]))
        people: dict[str, UUID] = {}
        made: list[tuple[UUID, list[str]]] = []

        def match(group: list[tuple[UUID, list[str]]], wanted: list[str]) -> UUID | None:
            found = {person for person, held in group if same(wanted, held)}
            if len(found) > 1:
                found = {person for person, held in group if held == wanted}
            return found.pop() if len(found) == 1 else None

        def person_of(year: int, printed: str) -> UUID:
            surname, _, given = SAME.get(printed, printed).partition(",")
            wanted = [*words(given), *words(surname)]
            key = slug(" ".join(wanted))
            if key in people:
                return people[key]
            for pattern, pinned in PINNED.items():
                if re.match(pattern, printed):
                    people[key] = pinned
                    return pinned
            for group in (*pools, made):
                found = match(group, wanted)
                if found is not None:
                    people[key] = found
                    return found
            hits = sorted({fec_id for fec_id, filed in roster[year] if same(wanted, filed)})
            if len(hits) == 1:
                people[key] = cs.key("fec.candidate", hits[0])
                if hits[0] not in fec_known:
                    fec_known.add(hits[0])
                    cs.add("person", id=people[key])
                    name(people[key], natural(printed))
            else:
                people[key] = cs.key(f"{SOURCE}.person", f"US:{key}")
                if people[key] in cs.minted:
                    cs.add("person", id=people[key])
                    name(people[key], natural(printed))
            made.append((people[key], wanted))
            return people[key]

        nationals: dict[tuple[int, str], UUID] = {}
        for (year, owner), lines in frame.filter(pl.col("owner").is_in(["D", "R"])).group_by(
            "year", "owner", maintain_order=True
        ):
            word = PARTIES[owner][1].removesuffix("-party")
            start = min(IOWA[year], *lines["day"])
            umbrella_key = f"{start}:{word}-presidential-primaries"
            umbrella = cs.key("us.election", umbrella_key)
            nationals[(year, owner)] = cs.key("us.contest", f"{start}:president:{word}")
            if umbrella_key in elections:
                continue
            elections.add(umbrella_key)
            cs.add(
                "election",
                id=umbrella,
                polity_id=cs.polity("US"),
                kind="primary",
                date_from=start,
                date_to=max(lines["day"]),
                status="held",
            )
            name(umbrella, f"{year} {word.title()} Party presidential primaries")
            cs.add("slug", path=f"us/{start}-{word}-presidential-primaries", entity_id=umbrella)
            cs.add(
                "contest",
                id=nationals[(year, owner)],
                election_id=umbrella,
                kind="delegate_allocation",
                office_id=cs.key("us.office", "president"),
                party_scope_id=cs.key("us.party", PARTIES[owner][1]),
                round_number=1,
                default_result_kind="counted",
            )
            name(nationals[(year, owner)], f"{year} {word.title()} presidential nomination")
            cs.add(
                "slug", path=f"us/{start}-{word}-presidential-primaries/president", entity_id=nationals[(year, owner)]
            )
        values: list[tuple[str, str, str | None, int, int]] = []
        loaded = 0
        for (year, state, day, owner), lines in frame.sort("year", "day", "state", "block").group_by(
            "year", "state", "day", "owner", maintain_order=True
        ):
            label, scope = PARTIES[owner]
            contest_key = f"{day}:{state}:president:{slug(label)}"
            if contest_key in keys or (year, state, owner) in SKIPPED:
                print(f"{contest_key}: already held or skipped")
                continue
            keys.add(contest_key)
            unit, area = states[state]
            state_name = STATES[state]
            election_key = f"{day}:{state}:primary"
            election = cs.key("us.election", election_key)
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day:%B} {day.day}, {year} {state_name} presidential primary election")
                if f"us/{state.lower()}/{day}-primary" not in slugs:
                    cs.add("slug", path=f"us/{state.lower()}/{day}-primary", entity_id=election)
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="delegate_allocation",
                office_id=cs.key("us.office", "president"),
                district_area_id=area,
                party_scope_id=cs.key("us.party", scope),
                round_number=1,
                allocation_parent_id=nationals.get((year, owner)),
                default_result_kind="counted",
            )
            name(contest, f"{year} {state_name} {label} presidential primary")
            path = f"us/{state.lower()}/{day}-primary/president-{slug(label)}"
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            loaded += 1
            printed = int(lines["total"][0])
            counted = int(lines["votes"].sum())
            if printed != counted:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"The FEC prints a total of {printed:,} votes, {abs(printed - counted):,} "
                        f"{'more' if printed > counted else 'fewer'} than the sum of its candidate lines, "
                        f"{counted:,}. The candidate lines are stored as printed."
                    ),
                )
            if (year, state) in CAVEATS:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEATS[(year, state)])
            options: dict[str, str] = {}
            for printed_name, votes, write_in in lines.select("name", "votes", "write_in").iter_rows():
                measure = {"Blank": 6, "Null": 5}.get(printed_name)
                if measure is not None:
                    values.append((str(result_set), str(unit), None, measure, votes))
                    continue
                kind = option_kind(printed_name, write_in)
                display = re.sub(r"\s+", " ", printed_name).strip()
                option_key = f"{contest_key}:{kind}:{slug(display)}"
                if option_key in options:
                    raise ValueError(f"{option_key} twice")
                option = cs.key("us.option", option_key)
                options[option_key] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind=kind,
                    party_id=cs.key("us.party", scope) if kind == "candidate" else None,
                    ballot_label=display,
                )
                if kind == "candidate":
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:head"),
                        person_id=person_of(year, display),
                        option_id=option,
                        role="head",
                        ballot_name=display,
                        is_independent=False,
                    )
                values.append((str(result_set), str(unit), str(option), 1, votes))
            values.append(
                (
                    str(result_set),
                    str(unit),
                    None,
                    4,
                    int(lines.filter(~pl.col("name").is_in(["Blank", "Null"]))["votes"].sum()),
                )
            )
        print(f"{loaded} state presidential primaries")
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()


NICKNAMES = {
    "bill": "william",
    "bob": "robert",
    "pat": "patrick",
    "jerry": "edmund",
    "tom": "thomas",
    "al": "albert",
    "steve": "steven",
    "larry": "lawrence",
    "dick": "richard",
    "ted": "edward",
    "jack": "john",
    "chuck": "charles",
    "morry": "maurice",
    "gene": "eugene",
    "doug": "douglas",
}


def clean(printed: str) -> str:
    text = re.sub(r"^Yrite-In", "Write-In", printed.strip())
    text = re.sub(r"\b(13|I3)(?=[a-z])", "B", text)
    text = re.sub(r"\s+([.,])", r"\1", text)
    text = re.sub(r",(?=[^\s,])", ", ", text)
    text = re.sub(r"\b([A-Z]) (?=[a-z]{2,})", r"\1", text).rstrip(",")
    text = re.sub(r"^Doman,", "Dornan,", text)
    return FIXES.get(text, FIXES.get(printed, text))


def natural(printed: str) -> str:
    surname, *rest = [part.strip() for part in printed.split(",")]
    return " ".join([*rest[:1], surname, *rest[1:]]).strip()


def same(wanted: list[str], held: list[str]) -> bool:
    if len(wanted) < 2 or len(held) < 2 or wanted[-1] != held[-1] or not alike(wanted[0], held[0]):
        return False
    middle, other = wanted[1:-1], held[1:-1]
    return not (middle and other and middle[0][0] != other[0][0])


def words(text: str) -> list[str]:
    cleaned = re.sub(r'"[^"]*"|\([^)]*\)', " ", text)
    if not slug(cleaned):
        cleaned = text.replace('"', " ")
    return [word for word in slug(cleaned).split("-") if word and word not in {"jr", "sr", "ii", "iii", "iv"}]


def alike(a: str, b: str) -> bool:
    a, b = NICKNAMES.get(a, a), NICKNAMES.get(b, b)
    return a == b or (len(a) > 1 and len(b) > 1 and (a.startswith(b) or b.startswith(a)))


def option_kind(text: str, write_in: bool) -> str:
    plain = text.lower()
    if re.match(r"^(uncommitted|noncommitted|no preference|uninstructed)", plain):
        return "uncommitted"
    if re.match(r"^(none of|write-in \(none of)", plain):
        return "none_of_the_above"
    if write_in or re.match(r"^(write-in|scatter|miscellaneous|all others|others?$)", plain):
        return "write_in"
    return "candidate"
