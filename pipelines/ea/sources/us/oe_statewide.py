import io
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PARTIES = {
    "D": "DEMOCRAT",
    "R": "REPUBLICAN",
    "DFL": "DEMOCRATIC-FARMER-LABOR",
    "L": "LIBERTARIAN",
    "LBT": "LIBERTARIAN",
    "LIBERTARIN": "LIBERTARIAN",
    "G": "GREEN",
    "GP": "GREEN",
    "GRN": "GREEN",
    "GRE": "GREEN",
    "WGR": "GREEN",
    "C": "CONSTITUTION",
    "CST": "CONSTITUTION",
    "CNST": "CONSTITUTION",
    "CON": "CONSTITUTION",
    "I": "INDEPENDENT",
    "IND": "INDEPENDENT",
    "AI": "AMERICAN INDEPENDENT",
    "PF": "PEACE AND FREEDOM",
    "NLP": "NATURAL LAW",
    "UST": "U.S. TAXPAYERS",
    "WF": "WORKING FAMILIES",
    "WOR": "WORKING FAMILIES",
    "W": "WORKING FAMILIES",
    "RTL": "RIGHT TO LIFE",
    "IP": "INDEPENDENCE",
    "LMN": "LEGAL MARIJUANA NOW",
    "GLC": "GRASSROOTS",
    "P": "PROGRESSIVE",
    "PG": "PACIFIC GREEN",
    "AMC": "AMERICAN",
    "UNC": "UNITED CITIZENS",
    "CTRY": "COUNTRY",
    "BLUE ENIGM": "BLUE ENIGMA",
    "IND OF DEL": "INDEPENDENT PARTY OF DELAWARE",
    "INDEPENDENCE PARTY O": "INDEPENDENCE",
    "NOMINATED BY PETITION": "INDEPENDENT",
    "NONE": "",
    "NP-W": "",
    "WRITE-IN": "",
    "WRITE-IN GRN": "",
    "WI": "",
    "NA": "",
    "AME": "",
    "AMR": "",
}
STATE_PARTIES = {("NY", "CON"): "CONSERVATIVE", ("NY", "IND"): "INDEPENDENCE"}
METHOD = (
    "County totals are the sums of the rows in OpenElections' results file for the state and year, which is a "
    "volunteer transcription of the state's official returns. The winner shown is the candidate with the most votes "
    "in those sums."
)
MISSING = "OpenElections' file has no rows for {counties} in this race, so those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these sums, closer than transcription differences from the official canvass can be "
    "trusted to settle, so no winner is recorded."
)


def fetch() -> None:
    openelections.fetch()


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, year, path in openelections.picks():
        text = raw.cached(
            openelections.SOURCE,
            openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path),
        ).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            if column.strip().lower() not in renamed.values():
                renamed[column] = column.strip().lower()
        frame = frame.rename(renamed)
        if "county" not in frame.columns and {"jurisdiction", "reporting_level"} <= set(frame.columns):
            frame = frame.filter(pl.col("reporting_level") == "county").rename({"jurisdiction": "county"})
        if not {"county", "office", "candidate", "votes"} <= set(frame.columns):
            continue
        office = pl.col("office").fill_null("").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars()
        frames.append(
            frame.select(
                year=pl.lit(year),
                day=pl.lit(date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)),
                state=pl.lit(state),
                office=office.str.replace(r"\s*\((UNEXPIRED|SPECIAL)[^)]*\)$|\s+-?\s*UNEXPIRED.*$", ""),
                special=office.str.contains("SPECIAL|UNEXPIRED"),
                county=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips.get(state, {}), default=None),
                candidate=pl.col("candidate")
                .fill_null("")
                .str.to_uppercase()
                .str.replace_all(r"\s+", " ")
                .str.strip_chars(),
                party=(pl.col("party") if "party" in frame.columns else pl.lit(None, pl.String))
                .str.to_uppercase()
                .str.strip_chars()
                .str.replace(r"\s*[,/].*$", "")
                .str.replace(r" PARTY$", "")
                .str.replace(r"^(WRITE-?IN|NP\b).*$", "")
                .replace({code: name for (place, code), name in STATE_PARTIES.items() if place == state})
                .replace(PARTIES)
                .replace(medsl_statewide.LABELS),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = (
        pl.concat(frames)
        .filter(
            pl.col("votes").is_not_null(),
            pl.col("county").is_not_null(),
            pl.col("candidate") != "",
            ~pl.col("candidate").str.contains(openelections.NOT_COUNTED),
            ~pl.col("candidate").str.contains(medsl_statewide.PSEUDO),
        )
        .with_columns(
            party=pl.when(pl.col("party") != "").then("party"),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            candidate=pl.when(
                pl.col("candidate").str.contains(r"^[^,]+, [^,]+$")
                & ~pl.col("candidate").str.contains(r", (JR|SR|II|III|IV)\.?$")
            )
            .then(pl.col("candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1"))
            .otherwise("candidate"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
    )
    named = []
    for (office,) in frame.select("office").unique().iter_rows():
        for pattern, title in medsl_statewide.OFFICES:
            if (
                re.fullmatch(pattern, office)
                and title != "Governor"
                and "Justice" not in title
                and "Judge" not in title
            ):
                named.append((office, title))
                break
    frame = frame.join(pl.DataFrame(named, schema=["office", "title"], orient="row"), on="office").with_columns(
        post=pl.lit(None, pl.String),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code="county",
        measure=pl.lit(1, pl.Int16),
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    counties = pl.DataFrame(
        [(state, len(set(codes.values()))) for state, codes in fips.items()], schema=["state", "counties"], orient="row"
    )
    races = (
        frame.group_by(*contest, "candidate", "writein")
        .agg(pl.col("votes").sum(), reach=pl.col("county").filter(pl.col("votes") > 0).n_unique())
        .group_by(contest)
        .agg(
            pl.col("reach").max(),
            top=pl.col("votes").filter(~pl.col("writein")).sort(descending=True).head(2).sum() / pl.col("votes").sum(),
        )
        .join(
            frame.group_by(contest).agg(
                pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
                pl.col("magnitude").max(),
            ),
            on=contest,
            nulls_equal=True,
        )
        .join(counties, on="state")
        .filter(
            pl.col("reach") >= 0.9 * pl.col("counties"),
            (pl.col("title").is_in(list(medsl_statewide.EXECUTIVE)) | (pl.col("top") >= 0.8)),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.join(races.select(contest), on=contest, nulls_equal=True).group_by(
        *contest, "magnitude", "kind", "code", "county", "candidate", "party", "writein", "measure", maintain_order=True
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "returns.parquet")
    races.select(*contest, "magnitude", "present", "counties").write_parquet(PARSED / "races.parquet")
    print(races.group_by("state").len().sort("state").rows())


def emit() -> Path:
    states = pl.read_parquet(PARSED / "races.parquet")["state"].unique().sort().to_list()

    def cite(cs: Changeset) -> dict[str, UUID]:
        sources = {}
        for state in states:
            title = f"{state} county-level results"
            sources[state] = cs.source("OpenElections", title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
        for state, _, path in openelections.picks():
            if state in sources:
                cs.artifact(
                    raw.cached(
                        openelections.SOURCE,
                        openelections.REPOSITORY.format(
                            state=state.lower(), commit=openelections.COMMITS[state], path=path
                        ),
                    ),
                    sources[state],
                )
        return sources

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Statewide executive and board elections 2000–2014 by county from OpenElections' results files",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
