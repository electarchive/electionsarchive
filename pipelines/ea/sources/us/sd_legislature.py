import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.sources.us.sd_sos import PUBLISHER, SITE, TITLE, table, url
from ea.store import DATA, connect

SOURCE = "us.sd_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LONG = {
    "2008/2008_general_election_legislature_county_totals.aspx": date(2008, 11, 4),
    "2010/2010_general_election_legislature_by_county.aspx": date(2010, 11, 2),
}
WIDE = {
    "2000/2000_general_election_official_returns_legislature.aspx": date(2000, 11, 7),
    "2002/2002_legislature_official_returns.aspx": date(2002, 11, 5),
    "2004/2004_legislature_official_Returns.aspx": date(2004, 11, 2),
}
BLOCKS = {"2006/2006_general_election_legislature_official_returns.aspx": date(2006, 11, 7)}
UNNAMED = {"15": ["MINNEHAHA"]}
HEADING = re.compile(r"(RECOUNT )?(?:(SENATE|HOUSE) )?DISTRICT\s*#?\s*0*(\d+)\s*-?\s*([AB]?)", re.IGNORECASE)
PDFS = {
    "http://web.archive.org/web/2016id_/https://sdsos.gov/elections-voting/assets/2012%20Assets/2012generalelectionlegislaturebycounty.pdf": date(
        2012, 11, 6
    ),
    SITE + "2014/legislativeracesbycounty%20.pdf": date(2014, 11, 4),
}
TITLE_LINE = re.compile(r"State (Senator|Representatives?) - (?:District )?0*(\d+)([AB]?)")


def fetch() -> None:
    for path in [*LONG, *WIDE, *BLOCKS]:
        raw.fetch(SOURCE, url(path), suffix=".html")
    for pdf in PDFS:
        raw.fetch(SOURCE, pdf, suffix=".pdf")


def chamber(office: str, district: str, part: str) -> str:
    return f"{'senate' if office == 'Senator' else 'house'}:{district}" + (f"-{part.lower()}" if part else "") + ":g"


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["SD"]
    rows = []
    recounted: dict[tuple[date, str, str, str], int] = {}
    for path, day in LONG.items():
        for row in table(path, SOURCE):
            if len(row) < 6 or not row[5].isdigit():
                continue
            race, county, _, district, candidate, votes = row[:6]
            found = re.fullmatch(r"0*(\d+)([AB]?)", district)
            rows.append(
                (
                    day,
                    chamber(race.split()[1], found[1], found[2]),
                    fips[re.sub(r"[^a-z]", "", county.lower())],
                    candidate.upper(),
                    int(votes),
                )
            )
    for path, day in WIDE.items():
        counties: dict[str, list[str]] = {}
        block: dict | None = None
        recount: str | None = None
        for cells in [*table(path, SOURCE), ["DISTRICT #0"]]:
            if not cells:
                continue
            if found := HEADING.match(cells[0]):
                if block is not None and block["rows"]:
                    places = counties[block["district"]]
                    total = sum(sum(values) for _, values in block["rows"])
                    if total in block["totals"] or all(sum(values) in block["totals"] for _, values in block["rows"]):
                        rows.extend(
                            (day, block["chamber"], code, name, value)
                            for name, values in block["rows"]
                            for code, value in zip(places, values, strict=True)
                        )
                    else:
                        print(
                            f"{day.year} {block['chamber']}: counties sum to {total}, printed {block['totals']}, skipped"
                        )
                named = [
                    cell
                    for cell in cells[1:]
                    if cell and not re.match(r"(?i)totals?$|percent", cell) and not re.match(r"[\d.,%]+$", cell)
                ]
                district = found[3] + (f"-{found[4].lower()}" if found[4] else "")
                if named:
                    counties[district] = [fips[re.sub(r"[^a-z]", "", name.lower())] for name in named]
                recount = f"{found[2].lower()}:{district}:g" if found[1] and found[2] else None
                block = (
                    None
                    if found[1] or not found[2]
                    else {
                        "chamber": f"{found[2].lower()}:{district}:g",
                        "district": district,
                        "rows": [],
                        "totals": {int(cell) for cell in cells[1:] if re.fullmatch(r"\d+", cell)},
                    }
                )
                continue
            if recount is not None:
                places = counties[recount.split(":")[1]]
                if cells[0] and all(re.fullmatch(r"\d+", cell) for cell in cells[1 : 1 + len(places)]):
                    name = re.sub(r"\s*-\s*[A-Z]{1,3}$", "", cells[0]).upper()
                    for code, value in zip(places, cells[1 : 1 + len(places)], strict=True):
                        recounted[(day, recount, code, name)] = int(value)
                continue
            if block is None:
                continue
            places = counties.get(block["district"], [])
            if cells[0] and places and all(re.fullmatch(r"\d+", cell) for cell in cells[1 : 1 + len(places)]):
                name = re.sub(r"\s*-\s*[A-Z]{1,3}$", "", cells[0])
                block["rows"].append((name.upper(), [int(cell) for cell in cells[1 : 1 + len(places)]]))
                block["totals"] |= {int(cell) for cell in cells[1 + len(places) :] if re.fullmatch(r"\d+", cell)}
            elif not cells[0]:
                block["totals"] |= {int(cell) for cell in cells if re.fullmatch(r"\d+", cell)}
    rows = [
        (day, chamber, code, name, recounted.get((day, chamber, code, name), votes))
        for day, chamber, code, name, votes in rows
    ]
    for path, day in BLOCKS.items():
        district, places, chamber_key, names, counts = "", [], "", [], []
        results: dict[str, list[tuple[str, str, int]]] = {}
        for cells in [*table(path, SOURCE), ["SENATE"]]:
            if not cells:
                continue
            found = HEADING.match(cells[0])
            office = re.fullmatch(r"(?:RECOUNT )?(SENATE|HOUSE)", cells[0])
            if (found or office) and names:
                codes = [fips[re.sub(r"[^a-z]", "", place.lower())] for place in places]
                results[chamber_key] = [
                    (code, name, value)
                    for name, values in zip(names, counts, strict=True)
                    for code, value in zip(codes, values, strict=True)
                ]
                names, counts = [], []
            named = [
                cell
                for cell in cells[1:]
                if re.fullmatch(r"[A-Z][A-Z ]+", cell) and cell not in ("TOTAL", "PERCENTAGE")
            ]
            if found:
                part = found[4].lower()
                if not found[2]:
                    district = found[3]
                    places = named or UNNAMED[district]
                elif named:
                    places = named
                if found[2]:
                    chamber_key = f"{found[2].lower()}:{found[3]}" + (f"-{part}" if part else "") + ":g"
                continue
            if office:
                chamber_key = f"{office[1].lower()}:{district}:g"
                if named:
                    places = named
            numbers = [int(cell) for cell in cells[1:] if cell.isdigit()]
            if len(numbers) == 1 and not cells[0]:
                if numbers[0] != sum(sum(values) for values in counts):
                    print(
                        f"2006 {chamber_key}: candidates sum to {sum(sum(values) for values in counts)}, printed {numbers}"
                    )
            elif numbers:
                if len(numbers) != len(places) + 1 or sum(numbers[:-1]) != numbers[-1]:
                    raise ValueError(f"2006 {chamber_key}: {cells} does not fit {places}")
                counts.append(numbers[:-1])
            if cells[0] and not office:
                names.append(re.sub(r"\s*-\s*[A-Z]{1,3}$", "", cells[0]).upper())
            total = next((cell for cell in cells if cell.startswith("Total:")), None)
            if total and sum(sum(values) for values in counts) != int(total.removeprefix("Total:")):
                print(f"2006 {chamber_key}: candidates sum to {sum(sum(values) for values in counts)}, printed {total}")
        rows.extend(
            (day, key, code, name, value) for key, found_rows in results.items() for code, name, value in found_rows
        )
    for pdf, day in PDFS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, pdf).path), "-"], capture_output=True, check=True, text=True
        ).stdout
        starts = [0, 0]
        blocks: list[dict | None] = [None, None]
        for line in text.splitlines():
            titles = list(TITLE_LINE.finditer(line))
            if any(found.start() > 20 for found in titles):
                starts[1] = next(found.start() for found in titles if found.start() > 20)
            for column in (0, 1):
                piece = line[starts[column] : starts[1] if column == 0 and starts[1] else None]
                if found := TITLE_LINE.search(piece):
                    blocks[column] = {"chamber": chamber(*found.groups()), "header": [], "rows": []}
                    continue
                block = blocks[column]
                row = re.match(r"\s*(\S.*?)\s{2,}(\d+(?:\s+\d+)*)\s*$", piece)
                if block is None or not piece.strip():
                    continue
                if row is None:
                    block["header"].append(piece)
                elif row[1] != "TOTAL":
                    block["rows"].append(
                        (fips[re.sub(r"[^a-z]", "", row[1].lower())], [int(cell) for cell in row[2].split()])
                    )
                else:
                    blocks[column] = None
                    printed = [int(cell) for cell in row[2].split()]
                    places = [match.start() for match in re.finditer(r"\d+", piece)]
                    words: dict[int, list[str]] = {}
                    for text_line in block["header"]:
                        for word in re.finditer(r"\S+", text_line):
                            if (
                                word[0] != "County"
                                and (
                                    column_at := max(
                                        (i for i, x in enumerate(places) if x <= word.start() + 1), default=None
                                    )
                                )
                                is not None
                            ):
                                words.setdefault(column_at, []).append(word[0])
                    names = [" ".join(words.get(i, [])) for i in range(len(printed) - 1)]
                    sums = [sum(values[i] for _, values in block["rows"]) for i in range(len(printed))]
                    if (
                        sums != printed
                        or any(len(values) != len(printed) for _, values in block["rows"])
                        or not all(names)
                    ):
                        print(
                            f"{day.year} {block['chamber']}: {names} counties sum to {sums}, printed {printed}, skipped"
                        )
                        continue
                    rows.extend(
                        (day, block["chamber"], code, name.upper(), values[i])
                        for code, values in block["rows"]
                        for i, name in enumerate(names)
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("SD"),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("SD"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Dakota's legislative generals by county, 2000-2014 (Secretary of State election history)",
            polity="US-SD",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE,
                license="Public record",
            )
        for path in [*LONG, *WIDE, *BLOCKS]:
            cs.artifact(raw.cached(SOURCE, url(path)), source)
        for pdf in PDFS:
            cs.artifact(raw.cached(SOURCE, pdf), source)
        frame = pl.read_parquet(PARSED / "counties.parquet")
        drifted = (pl.col("day") == date(2002, 11, 5)) & (pl.col("chamber") == "house:26:g")
        medsl.attach(cs, conn, frame.filter(~drifted), {"SD": source}, strict=True)
        medsl.attach(
            cs,
            conn,
            frame.filter(drifted),
            {"SD": source},
            strict=True,
            authoritative="Source: the Secretary of State's official returns, whose candidate totals and grand total "
            "agree; the earlier set's figures run about 40 percent lower.",
        )
        return cs.write()
