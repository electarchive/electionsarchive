import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.mi_cenr_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = "http://web.archive.org/web/2020id_/http://miboecfr.nictusa.com/election/results/DATA/{}GEN_MI_CENR_BY_COUNTY.xls"
YEARS = [1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014]
PUBLISHER = "Michigan Department of State"
TITLE = "Official general election results by county"
OFFICES = {
    "03": ("Secretary of State", 1),
    "04": ("Attorney General", 1),
    "09": ("Member of the State Board of Education", 2),
    "10": ("Regent of the University of Michigan", 2),
    "11": ("Trustee of Michigan State University", 2),
    "12": ("Governor of Wayne State University", 2),
}
METHOD = (
    "County totals come from the Michigan Department of State's official general election results by county, "
    "archived from miboecfr.nictusa.com."
)


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, FILE.format(year), suffix=".txt")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%'"""
            ).fetchall()
        }
    frames = []
    for year in YEARS:
        frame = pl.read_csv(raw.cached(SOURCE, FILE.format(year)).path, separator="\t", infer_schema=False, quote_char=None)
        frames.append(
            frame.filter(
                pl.col("OfficeCode(Text)").is_in(list(OFFICES)) & pl.col("CandidateVotes").is_not_null()
            ).select(
                year=pl.col("ElectionDate").str.slice(0, 4).cast(pl.Int32),
                day=pl.col("ElectionDate").str.to_date(),
                office=pl.col("OfficeCode(Text)"),
                special=pl.col("OfficeDescription").str.contains("Partial Term"),
                county=pl.col("CountyName")
                .str.to_lowercase()
                .str.replace(r"^st\.? ", "st ")
                .str.replace(r"^gd\. ", "grand ")
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(counties, default=None),
                candidate=pl.concat_str(
                    pl.col("CandidateFirstName"),
                    pl.col("CandidateMiddleName"),
                    pl.col("CandidateLastName"),
                    separator=" ",
                    ignore_nulls=True,
                )
                .str.to_uppercase()
                .str.replace_all(r"\s+", " ")
                .str.strip_chars(),
                party=pl.col("PartyDescription").str.strip_chars().str.to_uppercase(),
                writein=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("") == "W",
                votes=pl.col("CandidateVotes").cast(pl.Int64),
            )
        )
    frame = pl.concat(frames)
    lost = frame.filter(pl.col("county").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['candidate'].head(5).to_list()}")
    year = pl.col("year")
    frame = (
        frame.with_columns(
            candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"),
            party=pl.when(~pl.col("writein") & (pl.col("party") != "")).then("party"),
            title=pl.col("office").replace_strict({code: title for code, (title, _) in OFFICES.items()}),
            magnitude=pl.col("office").replace_strict({code: seats for code, (_, seats) in OFFICES.items()}).cast(pl.Int16),
        )
        .group_by("year", "day", "title", "special", "magnitude", "county", "candidate", "party", "writein")
        .agg(pl.col("votes").sum())
        .select(
            year=year,
            day="day",
            state=pl.lit("MI"),
            title="title",
            post=pl.lit(None, pl.String),
            special="special",
            magnitude="magnitude",
            kind=pl.lit("county"),
            code=pl.lit("county"),
            county="county",
            candidate="candidate",
            party="party",
            writein="writein",
            measure=pl.lit(1, pl.Int16),
            votes="votes",
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(83),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes", descending=[False, False, True])
        .head(30)
        .rows()
    )
    print(frame.group_by("year", "title", "special").agg(pl.col("county").n_unique()).sort("year", "title").rows())


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
                url="https://miboecfr.nictusa.com/election/results/",
                license="Public record",
            )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, FILE.format(year)), source)
        return {"MI": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Michigan's 1998 secretary of state and attorney general and the 1998 to 2014 "
        "State Board of Education, University of Michigan regents, Michigan State trustees, and Wayne State governors "
        "by county from the Department of State's official results",
        cite=cite,
        method=METHOD,
    )
