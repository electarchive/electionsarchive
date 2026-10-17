import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.al_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/"
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election data"
WORKBOOKS = {
    "eaattorneygeneral1986-2010.xls": "Attorney General",
    "eaauditor1986-2010.xls": "Auditor",
    "eacommissionerag-and-ind1986-2010.xls": "Commissioner of Agriculture and Industries",
    "ealtgovernor1986-2010.xls": "Lieutenant Governor",
    "easecretaryofstate1986-2010_0.xls": "Secretary of State",
    "eatreasurer1986-2010.xls": "Treasurer",
    "eapsc1986-2012.xls": "Public Service Commissioner",
}
CANDIDATE = re.compile(
    r"(.+?)(?:\s*\(\s*([A-Z]{1,3})\s*\)|\s+-\s+([A-Z]{1,3}))|(W(?:RITE)?-?\s?INS?|WI)", re.IGNORECASE
)
PRECINCTS = "2010-general-precinctLevel.zip"
TYPOS = {("eaauditor1986-2010.xls", "Gen.02", "579889"): "579899"}
PARTIES = {"D": "DEMOCRATIC", "R": "REPUBLICAN", "L": "LIBERTARIAN", "I": "INDEPENDENT", "C": "CONSTITUTION"}
METHOD = (
    "County totals come from the Alabama Secretary of State's election history workbooks. Each candidate's county "
    "votes add up to the statewide total printed in the workbook."
)
MISSING = "The Secretary of State's workbook has no row for {counties} in this race; those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def fetch() -> None:
    for name in [*WORKBOOKS, PRECINCTS]:
        raw.fetch(SOURCE, SITE + name, suffix=Path(name).suffix)


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["AL"]
    rows: list[tuple[int, str, str | None, str, str | None, bool, int, str]] = []
    books = {
        workbook: {
            name: [
                [
                    None if cell is None else TYPOS.get((workbook, name, str(cell).strip()), str(cell).strip())
                    for cell in row
                ]
                for row in frame.rows()
            ]
            for name, frame in pl.read_excel(
                raw.cached(SOURCE, SITE + workbook).path,
                sheet_id=0,
                engine="calamine",
                has_header=False,
                raise_if_empty=False,
                infer_schema_length=0,
            ).items()
        }
        for workbook in WORKBOOKS
    }
    cleaned: dict[str, str] = {}
    names: dict[str, set[str]] = {}
    for sheets in books.values():
        for cells in sheets.values():
            for row in cells[:4]:
                for cell in row:
                    found = CANDIDATE.fullmatch(cell or "")
                    if found and found[1]:
                        parts = re.split(r",\s*", found[1].strip())
                        suffix = [
                            part for part in parts if re.fullmatch(r"(?:JR|SR|II|III|IV)\.?", part, re.IGNORECASE)
                        ]
                        rest = [part for part in parts if part not in suffix]
                        words = " ".join(reversed(rest) if len(rest) == 2 else rest).title().split()
                        cleaned[found[1]] = " ".join([*words, *suffix])
                        if len(words) > 1:
                            names.setdefault(words[-1].lower(), set()).add(cleaned[found[1]])
    with zipfile.ZipFile(raw.cached(SOURCE, SITE + PRECINCTS).path) as archive:
        ballot = archive.read("Autauga.xls").decode("utf-8-sig")
    for text in set(re.findall(r">([^<>]*[A-Za-z][^<>]* [^<>]+)<", ballot)):
        words = [
            word
            for word in re.sub(r"[^A-Za-z. ]", " ", text).split()
            if not re.fullmatch(r"(?:JR|SR|II|III|IV)\.?", word, re.IGNORECASE)
        ]
        if len(words) > 1:
            names.setdefault(f"2010:{words[-1].lower()}", set()).add(
                re.sub(r",\s*(?=(?:JR|SR)\.?$)", " ", text).title()
            )
    for workbook, office in WORKBOOKS.items():
        sheets = books[workbook]
        for sheet, cells in sheets.items():
            year = re.search(r"(\d{2}|\d{4})\s*$", sheet.strip())
            if "gen" not in sheet.lower() or year is None or not 2002 <= 2000 + int(year[1][-2:]) <= 2012:
                continue
            text = " ".join([sheet, *(cell for row in cells[:3] for cell in row if cell)]).upper()
            header = next(
                index
                for index, row in enumerate(cells)
                if sum(CANDIDATE.fullmatch(cell or "") is not None for cell in row) >= 2
            )
            blocks: list[list[int]] = []
            closed = True
            for column, cell in enumerate(cells[header]):
                found = CANDIDATE.fullmatch(cell or "")
                if found and not closed:
                    blocks[-1].append(column)
                elif found:
                    blocks.append([column])
                closed = found is None or found[4] is not None
            for number, block in enumerate(blocks, 1):
                label = max(
                    column
                    for column in range(block[0])
                    if any(re.sub(r"[^a-z]", "", (row[column] or "").lower()) == "autauga" for row in cells)
                )
                counties = [
                    fips[plain] for row in cells if (plain := re.sub(r"[^a-z]", "", (row[label] or "").lower())) in fips
                ]
                numeric = [
                    [int(row[column] or 0) for column in block]
                    for row in cells[header + 1 :]
                    if all(re.fullmatch(r"\d*", row[column] or "") for column in block)
                    and any(row[column] for column in block)
                ]
                total = max(numeric)
                data = [values for values in numeric if values != total]
                if (
                    len(counties) != 67
                    or len(data) != 67
                    or [sum(column) for column in zip(*data, strict=True)] != total
                ):
                    raise ValueError(f"{workbook} {sheet} block {number}: {len(counties)} counties, {len(data)} rows")
                if "PRES" in text:
                    title, post = "President of the Public Service Commission", None
                elif office == "Public Service Commissioner":
                    seat = re.search(r"#(\d)", sheet) or re.findall(r"(?:PL\.|PLACE)\s*(\d)", text)
                    title, post = office, (seat[1] if isinstance(seat, re.Match) else seat[number - 1])
                else:
                    title, post = office, None
                for column, index in enumerate(block):
                    found = CANDIDATE.fullmatch(cells[header][index] or "")
                    assert found is not None
                    if found[4]:
                        candidate, party, writein = "WRITE-INS", None, True
                    else:
                        candidate = cleaned[found[1]]
                        for known in (names.get(f"20{year[1][-2:]}:{candidate.lower()}"), names.get(candidate.lower())):
                            if len(candidate.split()) == 1 and known and len({name.split()[0] for name in known}) == 1:
                                candidate = max(known, key=len)
                        party, writein = PARTIES.get((found[2] or found[3]).upper()), False
                    for county, values in zip(counties, data, strict=True):
                        rows.append(
                            (
                                2000 + int(year[1][-2:]),
                                title,
                                post,
                                candidate.upper(),
                                party,
                                writein,
                                values[column],
                                county,
                            )
                        )
    year = pl.col("year").cast(pl.Int32)
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "title": pl.String,
            "post": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
            "county": pl.String,
        },
        orient="row",
    ).select(
        year=year,
        day=pl.struct(year=year).map_elements(
            lambda row: date(row["year"], 11, 2 + (-date(row["year"], 11, 1).weekday()) % 7), return_dtype=pl.Date
        ),
        state=pl.lit("AL"),
        title="title",
        post="post",
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party="party",
        writein="writein",
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(67),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "post", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "post", "votes", descending=[False, False, False, True])
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.alabama.gov/alabama-votes/voter/election-data",
                license="Public record",
            )
        for name in [*WORKBOOKS, PRECINCTS]:
            cs.artifact(raw.cached(SOURCE, SITE + name), source)
        return {"AL": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Alabama's statewide executive offices and public service commissioners 2002–2012 by county from the "
        "Secretary of State's election history workbooks",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
        declared={
            "2002-11-05:AL:secretary-of-state": (
                "Nancy L. Worley",
                (
                    "Nancy Worley leads by 1,989 votes in these totals. She was certified the winner and took office in "
                    "January 2003."
                ),
            )
        },
    )
