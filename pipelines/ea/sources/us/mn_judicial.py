from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, mn_sos
from ea.store import DATA

SOURCE = "us.mn_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Minnesota Secretary of State"
TITLE = "General election results by precinct and county"
BASE_2020 = "https://electionresultsfiles.sos.mn.gov/20201103/"
DAYS = {2002: date(2002, 11, 5), 2004: date(2004, 11, 2), 2020: date(2020, 11, 3)}
SEAT_2002 = ("Seat 3", ("Paul H. Anderson", "Jack Baker", "WRITE-IN"))
COLUMNS_2004 = {
    "Seat 2": (("Helen Meyer", "AJSC2HelenMeyer"), ("WRITE-IN", "AJSC2WI")),
    "Seat 5": (("Sam Hanson", "AJSC5SamHanson"), ("WRITE-IN", "AJSC5WI")),
    "Seat 6": (
        ("Alan Page", "AJSC6AlanPage"),
        ("Sam Tinglestad", "AJSC6SamTinglestad"),
        ("WRITE-IN", "AJSC6WI"),
    ),
}
TITLE_SC = "Associate Justice of the Supreme Court"
METHOD = (
    "County totals are the sums of the precinct rows in the Secretary of State's general election results spreadsheet."
)
MISSING = "The file has no row for {counties} in this race; those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def fetch() -> None:
    raw.fetch(SOURCE, BASE_2020 + "allracesbycounty.txt", suffix=".txt")
    raw.fetch(SOURCE, BASE_2020 + "Cntytbl.txt", suffix=".txt")


def sheet(year: int) -> pl.DataFrame:
    path = raw.cached("us.mn_sos", mn_sos.MEDIA + mn_sos.FILES[year]).path
    return pl.read_excel(path, engine="calamine", read_options={"header_row": None}, infer_schema_length=0)


def number(value: object) -> int:
    return int(str(value).replace(",", "").strip() or 0)


def county(code: object) -> str:
    return "27" + str(int(str(code)) * 2 - 1).zfill(3)


def parse() -> None:
    rows: list[tuple[int, str, str, str, int]] = []
    first = sheet(2002)
    head = first.row(2)
    cc = head.index("CC")
    post, names = SEAT_2002
    for offset, name in enumerate(names):
        for cells in first.slice(3).iter_rows():
            if str(cells[cc]).strip().isdigit():
                rows.append((2002, post, name, county(cells[cc]), number(cells[76 + offset])))
    second = sheet(2004)
    head = second.row(0)
    cc = head.index("CC")
    for post, candidates in COLUMNS_2004.items():
        for name, label in candidates:
            column = head.index(label)
            for cells in second.slice(1).iter_rows():
                if str(cells[cc]).strip().isdigit():
                    rows.append((2004, post, name, county(cells[cc]), number(cells[column])))
    text = raw.cached(SOURCE, BASE_2020 + "allracesbycounty.txt").path.read_text(encoding="latin-1")
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(";")]
        if len(cells) > 13 and cells[3] == "7005" and cells[1]:
            name = "WRITE-IN" if cells[10] == "WI" else cells[7]
            rows.append((2020, "Seat 4", name, county(cells[1]), int(cells[13])))
    frame = pl.DataFrame(
        rows,
        schema={"year": pl.Int32, "post": pl.String, "candidate": pl.String, "county": pl.String, "votes": pl.Int64},
        orient="row",
    )
    frame = (
        frame.group_by("year", "post", "candidate", "county")
        .agg(pl.col("votes").sum())
        .with_columns(
            day=pl.col("year").replace_strict({year: day for year, day in DAYS.items()}, return_dtype=pl.Date),
            writein=pl.col("candidate") == "WRITE-IN",
            candidate=pl.col("candidate").str.to_uppercase(),
        )
    )
    frame = frame.select(
        "year",
        "day",
        state=pl.lit("MN"),
        title=pl.lit(TITLE_SC),
        post="post",
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party=pl.lit(None, pl.String),
        writein="writein",
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(87),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "post", "candidate")
        .agg(pl.col("votes").sum(), pl.col("county").n_unique())
        .sort("year", "post")
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
                url="https://www.sos.mn.gov/elections-voting/election-results/",
                license="Public record",
            )
        for year in (2002, 2004):
            cs.artifact(raw.cached("us.mn_sos", mn_sos.MEDIA + mn_sos.FILES[year]), source)
        cs.artifact(raw.cached(SOURCE, BASE_2020 + "allracesbycounty.txt"), source)
        return {"MN": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Minnesota's Supreme Court associate justice elections of 2002, 2004, and 2020 by county from the "
        "Secretary of State's results files",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
