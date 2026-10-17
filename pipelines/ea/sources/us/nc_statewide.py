import io
import re
import unicodedata
import zipfile
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, ncsbe
from ea.store import DATA, connect

SOURCE = "us.nc_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [day for day in ncsbe.DAYS if day.year % 4 == 0 and day.year > 2000]
OFFICES = {
    "LIEUTENANT GOVERNOR": "Lieutenant Governor",
    "SECRETARY OF STATE": "Secretary of State",
    "AUDITOR": "Auditor",
    "TREASURER": "Treasurer",
    "SUPER. OF PUBLIC INSTRUCTION": "Superintendent of Public Instruction",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "Superintendent of Public Instruction",
    "ATTORNEY GENERAL": "Attorney General",
    "COMMISSIONER OF AGRICULTURE": "Commissioner of Agriculture",
    "COMMISSIONER OF INSURANCE": "Commissioner of Insurance",
    "COMMISSIONER OF LABOR": "Commissioner of Labor",
}
ALIASES = {
    "BEVERLY EAVES PERDUE": "BEVERLY PERDUE",
    "ELAINE F. MARSHALL": "ELAINE MARSHALL",
    "JUNE S. ATKINSON": "JUNE ATKINSON",
    "JUNE ST. CLAIR ATKINSON": "JUNE ATKINSON",
    "CHERIE KILLIAN BERRY": "CHERIE BERRY",
    "RICHARD H. MOORE": "RICHARD MOORE",
}
PARTIES = {
    "DEM": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "GRE": "GREEN",
    "RFM": "REFORM",
    "UNA": "UNAFFILIATED",
}
METHOD = (
    "County totals are the sums of the precinct rows in the North Carolina State Board of Elections' official "
    "precinct results file."
)
MISSING = "The State Board of Elections' file has no rows for {counties} in this race; those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def fetch() -> None:
    ncsbe.fetch()


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    frames = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(ncsbe.SOURCE, ncsbe.FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0])
        first = text.split(b"\n", 1)[0]
        headed = first.lower().startswith((b'"county"', b"county"))
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            has_header=headed,
            new_columns=None if headed else ncsbe.HEADERLESS,
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        frame = frame.rename(
            {
                column: {**ncsbe.COLUMNS, "party_cd": "party", "party": "party"}[column.strip().lower()]
                for column in frame.columns
                if column.strip().lower() in {**ncsbe.COLUMNS, "party_cd": "party", "party": "party"}
            }
        )
        frames.append(
            frame.with_columns(office=pl.col("contest").str.strip_chars().str.to_uppercase().str.strip_prefix("NC "))
            .filter(pl.col("office").is_in(list(OFFICES)))
            .select(
                year=pl.lit(day.year, pl.Int32),
                day=pl.lit(day),
                state=pl.lit("NC"),
                title=pl.col("office").replace_strict(OFFICES),
                post=pl.lit(None, pl.String),
                special=pl.lit(False),
                magnitude=pl.lit(1, pl.Int16),
                kind=pl.lit("county"),
                code=pl.lit("county"),
                county=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.col("choice").str.to_uppercase().str.strip_chars().replace(ALIASES),
                party=pl.col("party").str.strip_chars().str.to_uppercase().replace_strict(PARTIES, default=None),
                writein=pl.col("choice").str.to_uppercase().str.contains("WRITE"),
                measure=pl.lit(1, pl.Int16),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames)
    if frame["county"].null_count():
        raise ValueError("unmatched counties")
    contest = ["year", "day", "state", "title", "post", "special"]
    frame = (
        frame.with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by([*contest, "magnitude", "kind", "code", "county", "candidate", "party", "writein", "measure"])
        .agg(pl.col("votes").sum())
        .sort([*contest, "county", "candidate"])
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(len(set(counties.values()))),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "candidate", "party")
        .agg(pl.col("votes").sum(), pl.col("county").n_unique())
        .sort("year", "title", "votes", descending=[False, False, True])
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(ncsbe.PUBLISHER, ncsbe.TITLE)
        if source in cs.minted:
            raise ValueError("no North Carolina State Board of Elections source")
        for day in DAYS:
            cs.artifact(raw.cached(ncsbe.SOURCE, ncsbe.FILES.format(day=day)), source)
        return {"NC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Carolina's Council of State elections 2004–2012 by county from the State Board of Elections' "
        "precinct results",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
        declared={
            "2004-11-02:NC:commissioner-of-agriculture": (
                "Steve Troxler",
                (
                    "Steve Troxler leads by 2,287 votes in these totals. More than 4,000 votes were lost in Carteret "
                    "County, and the race remained unresolved through three months of legal challenges until Britt Cobb "
                    "conceded on February 4, 2005. Troxler was sworn in on February 8, 2005."
                ),
            ),
            "2004-11-02:NC:superintendent-of-public-instruction": (
                "June Atkinson",
                (
                    "The State Board of Elections certified June Atkinson the winner on November 30, 2004. Bill Fletcher "
                    "contested the provisional ballots counted in the race; as the state constitution provides, the "
                    "General Assembly decided the contested election in a joint session in August 2005, declaring "
                    "Atkinson elected."
                ),
            ),
            "2012-11-06:NC:lieutenant-governor": (
                "Dan Forest",
                (
                    "The race was within 0.5% in these totals. The State Board of Elections certified Dan Forest the "
                    "winner, and he took office in January 2013."
                ),
            ),
        },
    )
