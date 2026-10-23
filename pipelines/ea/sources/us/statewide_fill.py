import io
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ca_sen_2022, md_sbe, medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.statewide_fill"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CA_OFFICES = {
    "Secretary of State": "Secretary of State",
    "Treasurer": "Treasurer",
    "Superintendent of Public Instruction": "Superintendent of Public Instruction",
}
MD_DAY = date(2018, 11, 6)
RI_PATH = "2018/20181106__ri__general__town.csv"
WA_PATH = "2020/20201103__wa__general__county.csv"
GA_PATHS = {
    date(2002, 11, 5): "2002/20021105__ga__general.csv",
    date(2006, 11, 7): "2006/20061107__ga__general.csv",
    date(2014, 11, 4): "2014/20141104__ga__general__county-level.csv",
}
GA_VOTES = ["election_day_votes", "advanced_votes", "absentee_by_mail_votes", "provisional_votes"]
OE_TITLE = "{state} precinct-level results"
CITIES = {"Cranston": "44007", "Pawtucket": "44007", "Providence": "44007", "Warwick": "44003"}
PARTIES = {
    "DEM": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "Democratic": "DEMOCRATIC",
    "Republican": "REPUBLICAN",
    "Democrat": "DEMOCRATIC",
    "Libertarian": "LIBERTARIAN",
    "Non-Partisan": "NONPARTISAN",
}
METHOD = (
    "County totals are the official county returns, or for Rhode Island the sums of the town returns, as "
    "published or transcribed from the state's canvass. The winner shown is the candidate with the most votes in "
    "those totals."
)
COUNTIES = {"CA": 58, "GA": 159, "MD": 24, "RI": 5, "WA": 39}
COLUMNS = ["year", "day", "state", "title", "county", "candidate", "party", "writein", "votes"]


def oe(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path)


def md_urls() -> list[str]:
    return [
        f"{md_sbe.ARCHIVE}{MD_DAY.year}/election_data/{name.replace('_2014_', f'_{MD_DAY.year}_')}"
        for name in md_sbe.files(2014)
    ]


def fetch() -> None:
    raw.fetch(ca_sen_2022.SOURCE, ca_sen_2022.URL, suffix=".xlsx")
    for url in md_urls():
        raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(SOURCE, oe("RI", RI_PATH), suffix=".csv")
    raw.fetch(SOURCE, oe("WA", WA_PATH), suffix=".csv")
    for path in GA_PATHS.values():
        raw.fetch(SOURCE, oe("GA", path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
        towns = {
            text.removesuffix(", RI"): code[:5]
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id
                   where u.scheme_code = 'us.town' and u.code like '44%'"""
            ).fetchall()
        } | CITIES
        md_fips = {
            re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))
            + ("city" if int(code[2:]) >= 500 else ""): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '24%'"""
            ).fetchall()
        }
    frames = [
        pl.read_excel(raw.cached(ca_sen_2022.SOURCE, ca_sen_2022.URL).path, engine="calamine", infer_schema_length=0)
        .filter(pl.col("Contest Name").is_in(list(CA_OFFICES)))
        .select(
            year=pl.lit(2022),
            day=pl.lit(date(2022, 11, 8)),
            state=pl.lit("CA"),
            title=pl.col("Contest Name").replace_strict(CA_OFFICES),
            county=pl.col("County Name").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips["CA"]),
            candidate="Candidate Name",
            party="Party Name",
            writein=pl.col("Write-in Flag") == "Y",
            votes=pl.col("Vote Total").cast(pl.Int64),
        )
    ]
    for url in md_urls():
        text = raw.cached(SOURCE, url).path.read_bytes()
        if text.lstrip().startswith(b"<"):
            raise ValueError(f"{url} is not a results file")
        county = re.sub(r"_County$", "", url.rsplit("/", 1)[1].removesuffix(f"_{MD_DAY.year}_General.csv"))
        code = md_fips[re.sub(r"[^a-z]", "", county.lower().replace("st.", "st").replace("saint", "st"))]
        frame = pl.read_csv(io.StringIO(text.decode("cp1252")), infer_schema=False)
        frames.append(
            frame.with_columns(pl.col("Office Name").str.strip_chars())
            .filter(pl.col("Office Name") == "Attorney General")
            .select(
                year=pl.lit(MD_DAY.year),
                day=pl.lit(MD_DAY),
                state=pl.lit("MD"),
                title=pl.lit("Attorney General"),
                county=pl.lit(code),
                candidate="Candidate Name",
                party="Party",
                writein=pl.col("Write-In?") == "Y",
                votes=pl.col("Total Votes").cast(pl.Int64),
            )
        )
    ri = pl.read_csv(io.BytesIO(raw.cached(SOURCE, oe("RI", RI_PATH)).path.read_bytes()), infer_schema_length=0)
    frames.append(
        ri.filter(pl.col("office") == "Secretary of State", pl.col("candidate") != "Total").select(
            year=pl.lit(2018),
            day=pl.lit(date(2018, 11, 6)),
            state=pl.lit("RI"),
            title=pl.lit("Secretary of State"),
            county=pl.col("county").replace_strict(towns),
            candidate="candidate",
            party="party",
            writein=pl.col("candidate") == "WRITE-IN",
            votes=pl.col("votes").cast(pl.Int64),
        )
    )
    wa = pl.read_csv(io.BytesIO(raw.cached(SOURCE, oe("WA", WA_PATH)).path.read_bytes()), infer_schema_length=0)
    frames.append(
        wa.filter(pl.col("office") == "Insurance Commissioner").select(
            year=pl.lit(2020),
            day=pl.lit(date(2020, 11, 3)),
            state=pl.lit("WA"),
            title=pl.lit("Insurance Commissioner"),
            county=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips["WA"]),
            candidate="candidate",
            party="party",
            writein=pl.col("candidate") == "Write-in",
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
        )
    )
    for day, path in GA_PATHS.items():
        ga = pl.read_csv(io.BytesIO(raw.cached(SOURCE, oe("GA", path)).path.read_bytes()), infer_schema_length=0)
        frames.append(
            ga.filter(pl.col("office") == "Public Service Commissioner").select(
                year=pl.lit(day.year),
                day=pl.lit(day),
                state=pl.lit("GA"),
                title=pl.lit("Public Service Commissioner"),
                post="district",
                county=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips["GA"]),
                candidate="candidate",
                party="party",
                writein=pl.lit(False),
                votes=pl.sum_horizontal(pl.col(GA_VOTES).cast(pl.Int64))
                if "votes" not in ga.columns
                else pl.col("votes").cast(pl.Int64),
            )
        )
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = (
        pl.concat(
            [frame.select(*COLUMNS, *(["post"] if "post" in frame.columns else [])) for frame in frames],
            how="diagonal_relaxed",
        )
        .with_columns(
            pl.col("year").cast(pl.Int32),
            candidate=pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase(),
            party=pl.when(~pl.col("writein")).then(pl.col("party").replace(PARTIES)),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            measure=pl.lit(1, pl.Int16),
        )
        .group_by(
            *contest,
            "magnitude",
            "kind",
            "code",
            "county",
            "candidate",
            "party",
            "writein",
            "measure",
            maintain_order=True,
        )
        .agg(pl.col("votes").sum())
    )
    races = returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.col("state").first().replace_strict(COUNTIES),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(races.select(*contest[:4], "counties", pl.col("present").list.len()))
    print(returns.group_by("state", "title", "candidate", "party").agg(pl.col("votes").sum()).sort("state", "votes"))


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        found = {
            "GA": ("OpenElections", "GA county-level results"),
            "CA": (ca_sen_2022.PUBLISHER, ca_sen_2022.TITLE),
            "MD": (md_sbe.PUBLISHER, md_sbe.TITLE),
            "RI": ("OpenElections", OE_TITLE.format(state="RI")),
            "WA": ("OpenElections", OE_TITLE.format(state="WA")),
        }
        sources = {state: cs.source(publisher, title) for state, (publisher, title) in found.items()}
        for state, source in sources.items():
            if source in cs.minted:
                raise ValueError(f"no source {found[state][1]}")
        cs.artifact(raw.cached(ca_sen_2022.SOURCE, ca_sen_2022.URL), sources["CA"])
        for url in md_urls():
            cs.artifact(raw.cached(SOURCE, url), sources["MD"])
        cs.artifact(raw.cached(SOURCE, oe("RI", RI_PATH)), sources["RI"])
        cs.artifact(raw.cached(SOURCE, oe("WA", WA_PATH)), sources["WA"])
        for path in GA_PATHS.values():
            cs.artifact(raw.cached(SOURCE, oe("GA", path)), sources["GA"])
        return sources

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="California's 2022 secretary of state, treasurer, and superintendent of public instruction, Maryland's "
        "2018 attorney general, Rhode Island's 2018 secretary of state, Washington's 2020 insurance commissioner, and "
        "Georgia's 2002, 2006, and 2014 public service commissioner generals by county",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
    )
