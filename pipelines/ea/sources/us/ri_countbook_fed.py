import itertools
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ri_countbook import BASE, BOOKS, COUNTIES, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.ri_countbook_fed"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PRIMARIES = {
    date(2002, 11, 5): date(2002, 9, 10),
    date(2004, 11, 2): date(2004, 9, 14),
    date(2006, 11, 7): date(2006, 9, 12),
    date(2010, 11, 2): date(2010, 9, 14),
}
TOWNS = {town: fips for fips, names in COUNTIES.items() for town in names}
HEAD = re.compile(
    r"^\s*US\s+(SENATE|SENATOR|CONGRESS)\b(?!.*(?:SUMMARY|…)).*?\bby\s+(?:COMMUNITY|MUNICIPALITY)\s*$", re.IGNORECASE
)
ROW = re.compile(r"^\s*([A-Z][A-Z .]+?)\s*\*?\s{2,}((?:[\d,]+|N/A)(?:\s+(?:[\d,]+|N/A))*)\s*$")


COUNTY_NAMES = {"BRISTOL": "44001", "KENT": "44003", "NEWPORT": "44005", "PROVIDENCE": "44007", "WASHINGTON": "44009"}
BOOK_2000 = "2000-Count-Book.pdf"
HEAD_2010 = re.compile(
    r"^\s*(?:US|U\.S\.) CONGRESS(?: -)? ?DISTRICT (\d)(?: [-–] (DEMOCRAT|REPUBLICAN) RESULTS| (?:ELECTION RESULTS )?by COUNTY)\s*$",
    re.IGNORECASE,
)


def parse_2010(lines: list[str]) -> list[tuple]:
    rows = []
    for index, line in enumerate(lines):
        found = HEAD_2010.match(line)
        if found is None or index < 150:
            continue
        district = int(found[1])
        party = found[2].lower() + ("ic" if found[2].upper() == "DEMOCRAT" else "") if found[2] else None
        header = []
        county = None
        subtotals: dict[str, list[int]] = {}
        towns: dict[str, dict[str, list[int]]] = {}
        for follow in lines[index + 1 : index + 80]:
            if re.match(r"^\s*STATE TOTALS?", follow, re.IGNORECASE):
                break
            if (m := re.match(r"^\s*([A-Z]+) COUNTY\s*$", follow)) and m[1] in COUNTY_NAMES:
                county = COUNTY_NAMES[m[1]]
            elif county is None:
                header.append(follow)
            elif m := re.match(r"^\s*County Total\s+([\d\s]+)$", follow):
                subtotals[county] = [int(x) for x in m[1].split()]
            elif (m := re.match(r"^\s*([A-Za-z][A-Za-z .]+?)\s{2,}([\d\s]+)$", follow)) and m[
                1
            ].strip().lower() in TOWNS:
                towns.setdefault(county, {})[m[1].strip().lower()] = [int(x) for x in m[2].split()]
        label = [h for h in header if h.strip()]
        position = next(i for i, h in enumerate(label) if "MUNICIPALITY" in h)
        surnames_line = label[position].replace("MUNICIPALITY", "")
        if not surnames_line.strip():
            surnames_line = label[position + 1] if party is None else label[position - 1]
        surnames = [m[0].strip() for m in re.finditer(r"\S+(?: \S+)*", surnames_line) if len(m[0].strip()) > 1]
        if party is None:
            parties = [m[1].upper() for h in label for m in re.finditer(r"\(([A-Za-z]+)\)", h)]
        else:
            parties = [party] * len(surnames)
        if party is None:
            day = date(2010, 11, 2)
            counties = {c: [sum(v[i] for v in t.values()) for i in range(len(surnames))] for c, t in towns.items()}
        else:
            day = date(2010, 9, 14)
            counties = subtotals
        print(2010, party or "general", district, surnames, parties, len(counties))
        for fips, votes in counties.items():
            for surname, vote in zip(surnames, votes, strict=True):
                rows.append(
                    (
                        2010,
                        "house" if party is None else f"house:{party}",
                        day,
                        False,
                        "RI",
                        district,
                        fips,
                        surname.upper(),
                        1,
                        False,
                        vote,
                        "county",
                    )
                )
    return rows


def parse_2000(lines: list[str]) -> list[tuple]:
    rows: list[tuple] = []
    general = next(i for i, l in enumerate(lines) if i > 1000 and "RHODE ISLAND GENERAL ELECTION" in l)
    for index, line in enumerate(lines):
        found = re.match(r"^\s*US (SENATE|CONGRESS)(?: District (\d))?\b.*\bby COUNTY\s*$", line, re.IGNORECASE)
        if found is None or index < 300:
            continue
        office = "senate" if found[1].upper() == "SENATE" else "house"
        district = int(found[2]) if found[2] else None
        primary = index < general
        day = date(2000, 9, 12) if primary else date(2000, 11, 7)
        header = lines[index + 1 : index + 10]
        label = next(h for h in header if "COMMUNITY" in h)
        surnames = label[label.index("COMMUNITY") + len("COMMUNITY") :].split()
        tags = next(h for h in header if re.search(r"\([A-Z/]+\)", h))
        parties = [
            "democratic" if tag[0] in "DO" else "republican" if tag[0] == "R" else None
            for tag in re.findall(r"\(([A-Z/]+)\)", tags)
        ]
        if len(parties) != len(surnames):
            raise ValueError(f"2000 {line.strip()}: {surnames} against {parties}")
        county = None
        towns: list[list[int]] = []
        blocks: list[tuple[str, list[int], list[int] | None]] = []
        for follow in lines[index + 1 : index + 160]:
            follow = re.sub(r"^\s*[Il:.,;'•|\[\]]{1,2}\s+(?=[A-Z])", " ", follow)
            if (named := re.match(r"^\s*([A-Z]+) COUNTY\s*$", follow)) is not None:
                county, towns = COUNTY_NAMES[named[1]], []
                continue
            values = [0 if v == "N/A" else int(v.replace(",", "")) for v in re.findall(r"\d[\d,]*|N/A", follow)]
            if re.match(r"^\s*COUNTY TOTALS?\b", follow):
                if county is None or len(values) != len(surnames):
                    raise ValueError(f"2000 {line.strip()}: county total {follow.strip()}")
                summed = (
                    [sum(column) for column in zip(*towns, strict=True)]
                    if towns and all(len(town) == len(values) for town in towns)
                    else None
                )
                blocks.append((county, values, summed))
                county = None
                continue
            if re.match(r"^\s*STATE TOTALS?\b", follow):
                break
            if county is not None and re.match(r"^\s*[A-Z][A-Z .]+?\s*\*?\s{2,}", follow) and values:
                towns.append(values)
            if re.search(r"by COMMUNITY|by COUNTY(?! \(continued\))", follow, re.IGNORECASE):
                raise ValueError(f"2000 {line.strip()}: no state total")
        if len(values) != len(surnames):
            raise ValueError(f"2000 {line.strip()}: state total {follow.strip()}")
        chosen: list[list[int]] = []
        for column, total in enumerate(values):
            options = [
                [block[1][column]] + ([block[2][column]] if block[2] and block[2][column] != block[1][column] else [])
                for block in blocks
            ]
            picks = [list(pick) for pick in itertools.product(*options) if sum(pick) == total]
            if not picks:
                raise ValueError(f"2000 {line.strip()} {surnames[column]}: no county mix ties {total}")
            chosen.append(max(picks, key=lambda pick: sum(a != b[1][column] for a, b in zip(pick, blocks))))
        for position, (county, _, _) in enumerate(blocks):
            for surname, party, column in zip(surnames, parties, chosen, strict=True):
                votes = column[position]
                if votes or not primary:
                    chamber = f"{office}:{party}" if primary else office
                    rows.append(
                        (2000, chamber, day, False, "RI", district, county, surname.upper(), 1, False, votes, "county")
                    )
        print(2000, "primary" if primary else "general", office, district, surnames)
    return rows


def fetch() -> None:
    for name in BOOKS.values():
        raw.fetch("us.ri_countbook", BASE + name, suffix=".pdf")
    raw.fetch("us.ri_countbook", BASE + BOOK_2000, suffix=".pdf")


def parse() -> None:
    rows = []
    for general, name in {**BOOKS, date(2000, 11, 7): BOOK_2000}.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached("us.ri_countbook", BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        lines = text.splitlines()
        if general.year == 2004:
            span = next(i for i, l in enumerate(lines) if i > 200 and "VOTE TOTALS FOR PRESIDENTIAL CANDIDATES" in l)
            end = next(i for i, l in enumerate(lines) if i > span and "RHODE ISLAND STATEWIDE PRIMARY" in l)
            columns = (
                [
                    ("democratic", n)
                    for n in ["KERRY", "EDWARDS", "DEAN", "KUCINICH", "LIEBERMAN", "CLARK", "UNCOMMIT", "LAROUCHE"]
                ]
                + [("republican", n) for n in ["BUSH", "UNCOMMIT"]]
                + [("green", n) for n in ["COBB", "MESPLAY", "UNCOMMIT"]]
            )
            tally: dict[tuple[str, str, str], int] = {}
            district_totals = [0] * len(columns)
            for follow in lines[span:end]:
                if t := re.match(r"^\s*District Total\s+([\d,\s]+)$", follow):
                    values = [int(x.replace(",", "")) for x in t[1].split()]
                    district_totals = [a + b for a, b in zip(district_totals, values, strict=True)]
                    continue
                m = re.match(r"^\s*(?:\d+\s+)?([A-Z][A-Z .]+?)\*?\s{2,}([\d,\s]+)$", follow)
                if m is None or m[1].strip().lower() not in TOWNS:
                    continue
                values = [int(x.replace(",", "")) for x in m[2].split()]
                if len(values) != len(columns):
                    print("president row", follow.strip(), len(values))
                    continue
                for (party, name), vote in zip(columns, values, strict=True):
                    key = (TOWNS[m[1].strip().lower()], party, name)
                    tally[key] = tally.get(key, 0) + vote
            day = date(2004, 3, 2)
            for (party, name), total in zip(columns, district_totals, strict=True):
                counted = sum(v for (_, p, n), v in tally.items() if (p, n) == (party, name))
                if total > counted:
                    tally[("RI:statewide", party, name)] = total - counted
            rows.extend(
                (
                    2004,
                    f"president:{party}",
                    day,
                    False,
                    "RI",
                    None,
                    fips,
                    name,
                    1,
                    False,
                    vote,
                    "aggregate" if fips == "RI:statewide" else "county",
                    3 if fips == "RI:statewide" else 0,
                    33 if fips == "RI:statewide" else 0,
                )
                for (fips, party, name), vote in tally.items()
            )
            print(2004, "president", len(tally))
        if general.year == 2010:
            rows.extend(parse_2010(lines))
            continue
        if general.year == 2000:
            rows.extend(parse_2000(lines))
            continue
        for index, line in enumerate(lines):
            found = HEAD.match(line)
            if found is None:
                continue
            office = "senate" if found[1].upper().startswith("SENAT") else "house"
            district = int(d[1]) if (d := re.search(r"District\s*#?(\d)", line, re.IGNORECASE)) else None
            towns: dict[str, list[int | None]] = {}
            header: list[str] = []
            for follow in lines[index + 1 : index + 90]:
                if HEAD.match(follow) or re.match(r"^\s*(?:DISTRICT |STATE )?TOTALS?\b", follow, re.IGNORECASE):
                    break
                row = ROW.match(follow)
                name = (
                    re.sub(
                        r"^([NSEW])\. ",
                        lambda m: {"N": "NORTH ", "S": "SOUTH ", "E": "EAST ", "W": "WEST "}[m[1]],
                        row[1].strip(),
                    ).lower()
                    if row
                    else ""
                )
                if row is not None and name in TOWNS:
                    towns[name] = [None if t == "N/A" else int(t.replace(",", "")) for t in row[2].split()]
                elif not towns and follow.strip():
                    header.append(follow)
            if not towns:
                continue
            context = " ".join(lines[max(0, index - 8) : index + 8]).upper()
            primary = "PRIMARY" in context and "GENERAL ELECTION" not in " ".join(lines[max(0, index - 8) : index])
            width = max(len(v) for v in towns.values())
            label = next((h for h in header if re.search(r"\b(COMMUNITY|MUNICIPALITY)\b", h)), None)
            if label is None:
                print(general, line.strip(), "no label line")
                continue
            start = re.search(r"\b(COMMUNITY|MUNICIPALITY)\b", label).end()
            surnames = [
                (m.start(), m[0].strip()) for m in re.finditer(r"\S+(?: \S+)*", label[start:]) if len(m[0].strip()) > 1
            ]
            surnames = [(pos + start, text) for pos, text in surnames]
            if len(surnames) != width:
                print(general, line.strip(), "surnames", surnames, "against", width, "columns")
                continue
            parties = []
            for pos, _ in surnames:
                tag = next(
                    (
                        m[1]
                        for h in header
                        for m in re.finditer(r"\(([A-Za-z]+)\)", h)
                        if abs((m.start() + m.end()) / 2 - pos - 5) < 14
                    ),
                    None,
                )
                parties.append(tag)
            if primary:
                with connect() as conn:
                    owners = {}
                    for party in ("democratic", "republican"):
                        key = (
                            f"{PRIMARIES[general]}:RI:{office}:{party}"
                            if district is None
                            else f"{PRIMARIES[general]}:RI:house:{district}:{party}"
                        )
                        for (label,) in conn.execute(
                            """select coalesce(n.text, o.ballot_label) from external_id e
                               join contest_option o on o.contest_id = e.entity_id and o.parent_option_id is null
                               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                               left join name n on n.entity_id = cy.person_id and n.is_preferred
                               where e.scheme = 'us.contest' and e.value = %s""",
                            [key],
                        ).fetchall():
                            owners.setdefault(label.upper().replace(".", ""), party)
                parties = [
                    next(
                        (p for label, p in owners.items() if any(w[:4] == surname.upper()[:4] for w in label.split())),
                        None,
                    )
                    for _, surname in surnames
                ]
                if None in parties:
                    print(general, line.strip(), "no party for", surnames, owners)
                    continue
            day = PRIMARIES[general] if primary else general
            for column, ((_, surname), party) in enumerate(zip(surnames, parties, strict=True)):
                chamber = office if not primary else f"{office}:{party}"
                counties: dict[str, int] = {}
                for town, values in towns.items():
                    if values[column] is not None:
                        counties[TOWNS[town]] = counties.get(TOWNS[town], 0) + values[column]
                for fips, votes in counties.items():
                    rows.append(
                        (
                            day.year,
                            chamber,
                            day,
                            False,
                            "RI",
                            district,
                            fips,
                            surname.upper(),
                            1,
                            False,
                            votes,
                            "county",
                        )
                    )
            print(
                general.year, "primary" if primary else "general", office, district, [s for _, s in surnames], parties
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [row if len(row) == 14 else (*row, 0, 0) for row in rows],
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
            "kind": pl.String,
            "channel": pl.Int16,
            "flags": pl.Int16,
        },
        orient="row",
    ).with_columns(source=pl.lit("RI")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2000 to 2010 U.S. Senate and House primaries and generals by county from the "
            "Board of Elections' count books",
            polity="US-RI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.ri.gov/publications/election-publications",
            license="Public record",
        )
        for name in [*BOOKS.values(), BOOK_2000]:
            cs.artifact(raw.cached("us.ri_countbook", BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet"),
            {"RI": source},
            strict=True,
            authoritative="Source: the Board of Elections' count books, whose town rows are summed to counties.",
        )
        return cs.write()
