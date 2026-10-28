import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ne_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.nebraska.gov/sites/default/files/doc/elections/"
FILES = {
    date(2002, 5, 14): "2002/primary_canvass_2002.pdf",
    date(2004, 5, 11): "2004/2004_primary_canvass_master.pdf",
}
COUNTIES = [
    "Adams", "Antelope", "Arthur", "Banner", "Blaine", "Boone", "Box Butte", "Boyd", "Brown", "Buffalo", "Burt", "Butler",
    "Cass", "Cedar", "Chase", "Cherry", "Cheyenne", "Clay", "Colfax", "Cuming", "Custer", "Dakota", "Dawes", "Dawson",
    "Deuel", "Dixon", "Dodge", "Douglas", "Dundy", "Fillmore", "Franklin", "Frontier", "Furnas", "Gage", "Garden",
    "Garfield", "Gosper", "Grant", "Greeley", "Hall", "Hamilton", "Harlan", "Hayes", "Hitchcock", "Holt", "Hooker",
    "Howard", "Jefferson", "Johnson", "Kearney", "Keith", "Keya Paha", "Kimball", "Knox", "Lancaster", "Lincoln", "Logan",
    "Loup", "Madison", "McPherson", "Merrick", "Morrill", "Nance", "Nemaha", "Nuckolls", "Otoe", "Pawnee", "Perkins",
    "Phelps", "Pierce", "Platte", "Polk", "Red Willow", "Richardson", "Rock", "Saline", "Sarpy", "Saunders",
    "Scotts Bluff", "Seward", "Sheridan", "Sherman", "Sioux", "Stanton", "Thayer", "Thomas", "Thurston", "Valley",
    "Washington", "Wayne", "Webster", "Wheeler", "York",
]  # fmt: skip
CELL = re.compile(
    r"(?<![A-Za-z])(TOTAL|"
    + "|".join(sorted(map(re.escape, COUNTIES), key=len, reverse=True))
    + r")\*{0,2}(?![A-Za-z])((?:\s+\d[\d,]*)+)"
)
PARTY = re.compile(r"^\s*(Republican|Democratic|Libertarian|Nebraska|Green) Party\b")
DISTRICT = re.compile(r"^\s*District (One|Two|Three)\s*$")
NUMBER = {"One": 1, "Two": 2, "Three": 3}
PUBLISHER = "Nebraska Secretary of State"
TITLE = "Primary election canvass"


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def text(path: str) -> list[str]:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def parse() -> None:
    frames: list[tuple[date, str, int | None, str, str, str, int]] = []
    turnout: list[tuple[date, str, int, int]] = []
    for day, path in FILES.items():
        lines = text(path)
        start = next(i for i, line in enumerate(lines) if re.match(r"\s+(United States Senator|President of the United States)\s*$", line))
        end = next(i for i in range(start, len(lines)) if re.match(r"\s+(Governor|Public Service Commissioner)\s*$", lines[i]))
        chamber, district, party = "", None, ""
        sections: dict[tuple[str, int | None, str], dict] = {}
        header: list[str] = []
        for line in lines[start:end]:
            if re.match(r"\s+(United States Senator|President of the United States)\s*$", line):
                chamber, district, party = ("senate" if "Senator" in line else "president"), None, ""
            elif re.match(r"\s+Member of the U\.S\. House", line):
                chamber, district, party = "house", None, ""
            elif re.search(r"Senate Nomination|Member of Congress Nominations", line):
                party = ""
            elif found := DISTRICT.match(line):
                district, party = NUMBER[found[1]], ""
            elif found := PARTY.match(line):
                if found[1].lower() != party:
                    header = []
                party = found[1].lower()
                sections.setdefault(
                    (chamber, district, party), {"names": [], "tally": {}, "total": {}, "columns": None}
                )
            elif not party:
                continue
            else:
                section = sections[(chamber, district, party)]
                cells = list(CELL.finditer(line))
                if not cells:
                    if line.strip() and not re.fullmatch(r"\s*\d+\s*", line):
                        header.append(line)
                    continue
                for cell in cells:
                    numbers = [int(value.replace(",", "")) for value in cell[2].split()]
                    if cell[1] == "TOTAL":
                        if section["columns"] is None:
                            section["columns"] = [cell.start(2) + m.end() for m in re.finditer(r"\d[\d,]*", cell[2])]
                            names: list[list[str]] = [[] for _ in numbers]
                            for head in header:
                                second = [m.start() for m in re.finditer(r"\bCounty\b", head)][1:]
                                head = head[: second[0]] if second else head
                                for m in re.finditer(r"\S+(?: \S+)*", head):
                                    if m[0] in ("County", "Nonpartisan") or "Party" in m[0] or "Voters" in m[0]:
                                        continue
                                    center = (m.start() + m.end()) / 2
                                    names[min(range(len(numbers)), key=lambda i: abs(section["columns"][i] - center))].append(m[0])
                            section["names"] = [" ".join(parts) for parts in names]
                        target = section["total"]
                    else:
                        target = section["tally"].setdefault(cell[1], {})
                    if len(numbers) != len(section["names"] or numbers):
                        print(f"{day} {chamber} {district} {party}: {line.strip()}")
                        continue
                    target["sum"] = [a + b for a, b in zip(target.get("sum", [0] * len(numbers)), numbers, strict=True)]
                header = []
        for (kind, dist, party), section in sections.items():
            if not section["total"]:
                print(f"{day} {kind} {dist} {party}: no TOTAL row")
                continue
            width = len(section["names"])
            sums = [sum(row["sum"][i] for row in section["tally"].values()) for i in range(width)]
            if any(abs(a - b) > 1 for a, b in zip(sums, section["total"]["sum"], strict=True)):
                print(f"{day} {kind} {dist} {party}: county sums {sums} against TOTAL {section['total']['sum']}")
                continue
            for county, row in section["tally"].items():
                frames.extend((day, kind, dist, party, name, county, row["sum"][i]) for i, name in enumerate(section["names"]))
        registered = next(i for i, line in enumerate(lines) if re.match(r"\s+Number of Registered Voter", line) and i > 30)
        voting = next(i for i, line in enumerate(lines) if re.match(r"\s+(Number of People Voting|Total Voting by Party)", line) and i > 30)
        figures: dict[str, list[int]] = {}
        closing = next(
            i for i in range(voting + 1, len(lines)) if re.search(r"Voters by Location|Total Voting by Location|United States Senator|President of the United States", lines[i])
        ) if day.year == 2002 else start
        for index, (lo, hi, column) in enumerate(((registered, voting, 1), (voting, closing, 0))):
            for line in lines[lo:hi]:
                for cell in CELL.finditer(line):
                    numbers = [int(value.replace(",", "")) for value in cell[2].split()]
                    figures.setdefault(cell[1], [0, 0])[index] = figures.get(cell[1], [0, 0])[index] or numbers[column]
        total = figures.pop("TOTAL")
        counts = (sum(v[0] for v in figures.values()), sum(v[1] for v in figures.values()))
        if len(figures) != 93 or counts != tuple(total):
            print(f"{day} turnout: {len(figures)} counties, sums {counts} against {total}")
        turnout.extend((day, county, registered_, ballots) for county, (registered_, ballots) in figures.items())
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        frames, schema={"day": pl.Date, "kind": pl.String, "district": pl.Int16, "party": pl.String, "candidate": pl.String, "county": pl.String, "votes": pl.Int64},
        orient="row"
    ).write_parquet(PARSED / "counties.parquet")
    pl.DataFrame(turnout, schema=["day", "county", "registered", "ballots"], orient="row").write_parquet(PARSED / "turnout.parquet")
    print(len(frames), "candidate rows;", len(turnout), "turnout rows")


def emit() -> Path:
    voters = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): (code, unit)
            for code, unit, text in conn.execute(
                """select u.code, u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2002 and 2004 federal primaries by county, with registered voters and ballots cast",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.nebraska.gov/elections/election-results",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        frame = (
            pl.read_parquet(PARSED / "counties.parquet")
            .with_columns(
                chamber=pl.col("kind") + ":" + pl.col("party"),
                code=pl.col("county").map_elements(lambda name: counties[re.sub(r"[^a-z]", "", name.lower())][0], return_dtype=pl.String),
                year=pl.col("day").dt.year().cast(pl.Int32),
                special=pl.lit(False),
                state=pl.lit("NE"),
                measure=pl.lit(1, dtype=pl.Int16),
                writein=pl.lit(False),
                kind=pl.lit("county"),
                source=pl.lit("NE"),
            )
            .drop("party")
        )
        medsl.attach(cs, conn, frame, {"NE": source}, strict=True)
        for day in FILES:
            ((election,),) = {
                (election_id,)
                for (election_id,) in conn.execute(
                    """select c.election_id from external_id x join contest c on c.id = x.entity_id
                       where x.scheme = 'us.contest' and x.value like %s""",
                    [f"{day}:NE:%"],
                )
            }
            result_set = cs.key(f"{SOURCE}.turnout", str(day))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election not in held,
                completeness=1,
            )
            for county, registered, ballots in voters.filter(pl.col("day") == day).select("county", "registered", "ballots").iter_rows():
                for measure, value in ((2, registered), (3, ballots)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=UUID(str(counties[re.sub(r"[^a-z]", "", county.lower())][1])),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
