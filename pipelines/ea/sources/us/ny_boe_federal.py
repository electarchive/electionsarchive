import re
import subprocess
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ny_boe
from ea.store import DATA, connect

SOURCE = "us.ny_boe_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    ("senate", date(2006, 11, 7), False): ("20120822221205", "2006/general/2006_ussen.xls"),
    ("senate", date(2010, 11, 2), False): ("20121004080157", "2010/general/2010USSenator6YrRecertified09122012.xls"),
    ("senate", date(2010, 11, 2), True): ("20121004080201", "2010/general/2010USSenator2YrRecertified09122012.xls"),
    ("house", date(2010, 11, 2), False): ("20121004081147", "2010/general/2010CongressRecertified09122012.xls"),
    ("house", date(2018, 11, 6), False): ("20200218182300", "2018/general/2018Congress.xlsx"),
    ("house", date(2011, 9, 13), True): ("20120324155926", "2011/Special/SpecialElectionVoteResults09132011.pdf"),
}
DISTRICTS = {date(2010, 11, 2): {29}, date(2018, 11, 6): {18, 20, 21}, date(2011, 9, 13): {9}}
PARTY_WORDS = {
    "Republican",
    "Democratic",
    "Independence",
    "Conservative",
    "Working",
    "Families",
    "Green",
    "Socialist",
    "Equality",
    "Libertarian",
    "Workers",
    "County",
}
COUNTERS = {"Blank", "Void", "BVS", "Subtotal", "Total", "Blk.,Void"}


def fetch() -> None:
    for stamp, path in FILES.values():
        try:
            raw.cached(SOURCE, ny_boe.WAYBACK.format(stamp, path))
        except LookupError:
            raw.fetch(SOURCE, ny_boe.WAYBACK.format(stamp, path), suffix="." + path.rsplit(".", 1)[1])


def number(cell: str) -> bool:
    return re.fullmatch(r"\d+(\.0)?", cell) is not None


def county(label: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"^Part of | County( Vote Results)?$", "", label.strip()).lower())


def parse() -> None:
    rows: list[dict[str, object]] = []

    def add(chamber: str, day: date, special: bool, district: int | None, lines: list[tuple[str, str, int]]) -> None:
        rows.extend(
            {
                "chamber": chamber,
                "day": day,
                "special": special,
                "district": district,
                "county": place,
                "candidate": "WRITE-INS" if name == "Scattering" else name.upper(),
                "writein": name == "Scattering",
                "votes": votes,
            }
            for name, place, votes in lines
        )

    for (chamber, day, special), (stamp, path) in FILES.items():
        if path.endswith(".pdf"):
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, ny_boe.WAYBACK.format(stamp, path)).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split("RECAP:")[0]
            (district,) = DISTRICTS[day]
            places = [county(place) for place in re.findall(r"Part of \w+", text.split("CANDIDATE", 1)[1].splitlines()[0])]
            lines = []
            for found in re.finditer(r"^(?:[A-Z]{3})?\s+(\S.*?)\s{2,}((?:[\d,]+\s+)+[\d,]+)$", text, re.MULTILINE):
                *votes, total = [int(value.replace(",", "")) for value in found[2].split()]
                if len(votes) != len(places) or sum(votes) != total:
                    raise ValueError(f"{day} house {district} {found[1]}: {votes} do not add to {total}")
                if found[1] in ("BLANK", "VOID") or found[1].startswith("TOTAL"):
                    continue
                name = "Scattering" if found[1] == "SCATTERING" else found[1]
                lines.extend(zip([name] * len(places), places, votes, strict=True))
            add(chamber, day, special, district, lines)
            continue
        book = fastexcel.read_excel(raw.cached(SOURCE, ny_boe.WAYBACK.format(stamp, path)).path)
        if path.endswith(".xlsx"):
            for sheet in book.sheet_names:
                district = int(re.match(r"\d+", sheet)[0])
                if district not in DISTRICTS[day]:
                    continue
                cells = [
                    [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
                    for row in book.load_sheet(sheet, header_row=None).to_polars().cast(pl.String).iter_rows()
                ]
                header = cells[1]
                places = {n: county(text) for n, text in enumerate(header) if text.endswith("Vote Results")}
                party = header.index("Total Votes by Party")
                lines = []
                for row in cells[2:]:
                    if row[0].startswith("Total"):
                        break
                    found = re.fullmatch(r"(.+?) \(([A-Z]+)\)", row[0])
                    if row[0] in ("Blank", "Void") or not (found or row[0] == "Scattering"):
                        continue
                    if sum(int(row[n]) for n in places) != int(row[party]):
                        raise ValueError(f"{day} house {district} {row[0]}: counties do not add to {row[party]}")
                    lines.extend((found[1] if found else row[0], place, int(row[n])) for n, place in places.items())
                add(chamber, day, special, district, lines)
            continue
        cells = [
            [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
            for row in book.load_sheet(0, header_row=None).to_polars().cast(pl.String).iter_rows()
        ]
        district = None
        recap: list[str] | None = None
        end = 0
        for top, row in enumerate(cells):
            if found := re.match(r"(\d+)(st|nd|rd|th) CONGRESSIONAL DISTRICT", row[0], re.IGNORECASE):
                district = int(found[1])
            if row[0] == "RECAP":
                recap = row
            if row[0] != "County" or top < end or (chamber == "house" and district not in DISTRICTS[day]):
                continue
            columns: dict[int, str] = {}
            for n in range(1, len(row)):
                parts = [
                    cells[at][n]
                    for at in range(top - 1, top + 3)
                    if cells[at][n]
                    and not number(cells[at][n])
                    and cells[at][n] not in PARTY_WORDS
                    and not re.fullmatch(r"[A-Z]{2,4}(/[A-Z]{2,4})?", cells[at][n])
                ]
                if row[n] == "County" or not parts or any(word in COUNTERS for part in parts for word in part.split()):
                    continue
                columns[n] = "Scattering" if "Scattering" in parts else " ".join(parts)
            lines = []
            sums = dict.fromkeys(columns, 0)
            printed = None
            for end, line in enumerate(cells[top + 1 :], top + 1):
                if re.fullmatch(r"(?i)(statewide )?total", line[0]) or re.search(r"(?i)CONGRESSIONAL DISTRICT", line[0]):
                    printed = None if re.search(r"(?i)DISTRICT", line[0]) else line
                    break
                if not line[0] or line[0].startswith("Total") or not number(line[1]):
                    continue
                for n, name in columns.items():
                    lines.append((name, county(line[0]), int(float(line[n] or 0))))
                    sums[n] += lines[-1][2]
            printed = printed or recap
            if printed is None:
                raise ValueError(f"{day} {chamber} {district}: no printed total")
            for n, name in columns.items():
                if number(printed[n]) and sums[n] != int(float(printed[n])):
                    raise ValueError(f"{day} {chamber} {district} {name}: counties sum to {sums[n]}, printed {printed[n]}")
            add(chamber, day, special, district if chamber == "house" else None, lines)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("day", "chamber", "special", "district", "candidate")
        .agg(pl.col("county").n_unique(), pl.col("votes").sum())
        .sort("day", "chamber", "district", "votes", descending=[False, False, False, True])
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's 2010 U.S. Senate elections and 2010, 2011, and 2018 U.S. House elections by county, "
            "from the State Board of Elections",
            polity="US-NY",
        )
        source = cs.source(ny_boe.PUBLISHER, ny_boe.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ny_boe.PUBLISHER,
            title=ny_boe.TITLE,
            url="https://" + ny_boe.ORIGIN,
            license="Public record",
        )
        for stamp, path in FILES.values():
            cs.artifact(raw.cached(SOURCE, ny_boe.WAYBACK.format(stamp, path)), source)
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.select(
                year=pl.col("day").dt.year().cast(pl.Int32),
                chamber="chamber",
                day="day",
                special="special",
                state=pl.lit("NY"),
                district=pl.col("district").cast(pl.Int16),
                code=pl.col("county").replace_strict(codes),
                candidate="candidate",
                measure=pl.lit(1, pl.Int16),
                writein="writein",
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("NY"),
            ),
            {"NY": source},
            strict=True,
        )
        return cs.write()
