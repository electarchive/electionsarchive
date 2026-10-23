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
VERSION = "1"
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


def fetch() -> None:
    for name in BOOKS.values():
        raw.fetch("us.ri_countbook", BASE + name, suffix=".pdf")


def parse() -> None:
    rows = []
    for general, name in BOOKS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached("us.ri_countbook", BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        lines = text.splitlines()
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
                name = re.sub(r"^([NSEW])\. ", lambda m: {"N": "NORTH ", "S": "SOUTH ", "E": "EAST ", "W": "WEST "}[m[1]], row[1].strip()).lower() if row else ""
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
            surnames = [(m.start(), m[0].strip()) for m in re.finditer(r"\S+(?: \S+)*", label[start:]) if len(m[0].strip()) > 1]
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
                        key = f"{PRIMARIES[general]}:RI:{office}:{party}" if district is None else f"{PRIMARIES[general]}:RI:house:{district}:{party}"
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
                    next((p for label, p in owners.items() if any(w[:4] == surname.upper()[:4] for w in label.split())), None)
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
                    rows.append((day.year, chamber, day, False, "RI", district, fips, surname.upper(), 1, False, votes, "county"))
            print(general.year, "primary" if primary else "general", office, district, [s for _, s in surnames], parties)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
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
        },
        orient="row",
    ).with_columns(source=pl.lit("RI")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2002 to 2010 U.S. Senate and House primaries and generals by county from the "
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
        for name in BOOKS.values():
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
