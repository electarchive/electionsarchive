import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_cvr"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
RESULTS = "https://www.vote.nyc/sites/default/files/pdf/election_results"
CVRS = {
    date(2021, 6, 22): f"{RESULTS}/2021/20210622Primary%20Election/cvr/PE2021_CVR_Final.zip",
    date(2025, 6, 24): f"{RESULTS}/2025/20250624Primary%20Election/rcv/2025_Primary_CVR_2025-07-17.zip",
}
CHOICE = re.compile(r"^(\w+) (.+?) Choice (\d) of (\d) (.+) \((\d)(\d{5})\)$")
PRECINCT = re.compile(r"^AD: (\d\d) ED: (\d\d\d)$")
WRITE_IN = "Write-in"
WRITE_INS = "Write-ins"
INACTIVE = "Inactive ballots"
ALIASES = {"Selvena N. Brooks-Powers": "Selvena Brooks-Powers", "Nabaraj KC": "Nabaraj Kc"}
RECERTIFIED = {
    (date(2021, 6, 22), "024347"): (
        date(2021, 8, 17),
        19,
        " The Board's report also counts 2,469 ballots as inactive from the first round, which the records do not.",
    ),
    (date(2021, 6, 22), "024367"): (
        date(2021, 8, 24),
        8,
        " The Board's report also counts 233 ballots as inactive from the first round, which the records do not.",
    ),
    (date(2021, 6, 22), "024425"): (date(2021, 8, 24), 2, ""),
    (date(2021, 6, 22), "024496"): (date(2021, 8, 24), 2, ""),
}
PUBLISHER = "Board of Elections in the City of New York"
METHOD = (
    "Tabulated by electionsarchive.com from the Board of Elections' cast vote records, election district by election "
    "district. Skipped rankings are passed over, a ballot exhausts at its first overvote, and a repeated candidate "
    "counts once. Each round drops the candidates the Board's official count dropped in that round, so the order of "
    "eliminations is the Board's: undeclared write-ins first, then the largest group of trailing candidates whose "
    "combined votes fall short of the next candidate's. Each candidate's value at round n is their running total; an "
    "eliminated candidate has 0 at the round of their elimination."
)
EXACT = "Summed over the contest's election districts, every round reproduces the Board's official rounds exactly."


def fetch() -> None:
    for link in CVRS.values():
        raw.fetch(SOURCE, link, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for day, link in CVRS.items():
        archive = zipfile.ZipFile(raw.cached(SOURCE, link).path)
        names = archive.namelist()
        labels = {
            candidacy: ALIASES.get(" ".join(label.split()), " ".join(label.split()))
            for candidacy, label in fastexcel.read_excel(
                archive.read(next(name for name in names if name.endswith("CandidacyID_To_Name.xlsx")))
            )
            .load_sheet(0, dtypes="string")
            .to_polars()
            .iter_rows()
        }
        frames = []
        for name in names:
            if not re.search(r"_(ABS|AFF|EAR|ELE|EMG|OTH)[^/]*\.xlsx$", name):
                continue
            reader = fastexcel.read_excel(archive.read(name))
            columns = {
                column.name: match
                for column in reader.load_sheet(0, n_rows=0).available_columns()
                if (match := CHOICE.match(column.name))
            }
            sheet = reader.load_sheet(
                0, use_columns=["Cast Vote Record", "Precinct", *columns], dtypes="string"
            ).to_polars()
            frames.append(
                sheet.unpivot(index=["Cast Vote Record", "Precinct"], variable_name="column", value_name="value")
                .drop_nulls("value")
                .select(
                    ballot=pl.lit(name.rsplit("/", 1)[-1]) + ":" + pl.col("Cast Vote Record"),
                    precinct="Precinct",
                    page=pl.col("column").replace_strict({column: f"0{m[7]}" for column, m in columns.items()}),
                    rank=pl.col("column").replace_strict(
                        {column: int(m[3]) for column, m in columns.items()}, return_dtype=pl.Int8
                    ),
                    value="value",
                )
            )
        ballots = pl.concat(frames)
        precincts = ballots["precinct"].unique()
        if not precincts.str.contains(PRECINCT.pattern).all():
            raise ValueError(
                f"{day}: unreadable precincts {precincts.filter(~precincts.str.contains(PRECINCT.pattern))}"
            )
        ballots.with_columns(
            ed=pl.col("precinct").str.replace(PRECINCT.pattern, "$1$2"),
            choice=pl.col("value").replace({**labels, WRITE_IN: WRITE_INS}),
        ).drop("precinct", "value").write_parquet(PARSED / f"ballots_{day}.parquet")

    official = pl.read_parquet(DATA / "parsed" / "us.nyc_boe" / "rounds.parquet")
    tallies = []
    for day in CVRS:
        ballots = pl.read_parquet(PARSED / f"ballots_{day}.parquet")
        for (page,) in official.filter(pl.col("date") == day).select("page").unique().iter_rows():
            schedule = (
                official.filter((pl.col("date") == day) & (pl.col("page") == page) & (pl.col("label") != INACTIVE))
                .group_by("round")
                .agg(pl.col("label"))
                .sort("round")
            )
            rounds = [set(labels) for labels in schedule["label"]]
            writein = next(
                (label for label in rounds[0] if label.replace("-", "").lower() in ("writein", "writeins")), WRITE_INS
            )
            marks = (
                ballots.filter(pl.col("page") == page)
                .with_columns(choice=pl.col("choice").replace({WRITE_INS: writein}))
                .sort("ballot", "rank")
            )
            cuts = marks.filter(pl.col("choice") == "overvote").group_by("ballot").agg(cut=pl.col("rank").min())
            ranked = (
                marks.join(cuts, on="ballot", how="left")
                .filter(
                    (pl.col("cut").is_null() | (pl.col("rank") < pl.col("cut"))) & (pl.col("choice") != "undervote")
                )
                .unique(["ballot", "choice"], keep="first", maintain_order=True)
                .select("ballot", "ed", "rank", "choice")
            )
            counted = ranked.group_by("ballot").agg(pl.col("ed").first())
            previous = None
            for number, continuing in enumerate(rounds, 1):
                top = (
                    ranked.filter(pl.col("choice").is_in(list(continuing)))
                    .group_by("ballot", maintain_order=True)
                    .agg(pl.col("ed").first(), pl.col("choice").first())
                )
                inactive = counted.join(top, on="ballot", how="anti").with_columns(choice=pl.lit(INACTIVE))
                counts = (
                    pl.concat([top, inactive])
                    .group_by("ed", "choice")
                    .agg(votes=pl.len())
                    .with_columns(date=pl.lit(day), page=pl.lit(page), round=pl.lit(number))
                )
                tallies.append(counts)
                if previous is not None:
                    tallies.append(
                        previous.filter(pl.col("choice").is_in(list(rounds[number - 2] - continuing))).with_columns(
                            votes=pl.lit(0, pl.UInt32), round=pl.lit(number)
                        )
                    )
                previous = counts
    tally = pl.concat(tallies, how="vertical_relaxed")
    citywide = (
        tally.group_by("date", "page", "round", "choice")
        .agg(pl.col("votes").sum().cast(pl.Int64))
        .filter(pl.col("votes") > 0)
        .rename({"choice": "label"})
    )
    differences = citywide.join(
        official.select("date", "page", "round", "label", "votes"),
        on=["date", "page", "round", "label"],
        how="full",
        coalesce=True,
        suffix="_official",
    ).filter(
        (pl.col("votes").fill_null(0) != pl.col("votes_official").fill_null(0))
        & ~pl.struct("date", "page", "label", "votes", "votes_official").map_elements(
            lambda row: (
                (key := (row["date"], row["page"])) in RECERTIFIED
                and (
                    row["label"] == INACTIVE
                    or abs((row["votes_official"] or 0) - (row["votes"] or 0)) <= RECERTIFIED[key][1]
                )
            ),
            return_dtype=pl.Boolean,
        )
    )
    if differences.height:
        raise ValueError(
            f"the CVR tabulation differs from the official rounds:\n{differences.sort('date', 'page', 'round')}"
        )
    tally.write_parquet(PARSED / "rounds.parquet")


def emit() -> Path:
    tally = pl.read_parquet(PARSED / "rounds.parquet").join(
        pl.read_parquet(DATA / "parsed" / "us.nyc_boe" / "rounds.parquet")
        .select("date", "page", "key", "office", "district")
        .unique(),
        on=["date", "page"],
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's ranked-choice primaries by election district, tabulated from cast vote records",
            polity="US",
        )
        city = cs.key("us.polity", "new-york-city")
        for day, link in CVRS.items():
            frame = tally.filter(pl.col("date") == day)
            if frame.is_empty():
                continue
            title = f"{day.year} Primary Election: Cast Vote Records"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=link,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, link), source)
            places = {
                code: (area, county)
                for code, area, county in conn.execute(
                    """select c.code, c.area_id, u.id from area_code c
                       join area a on a.id = c.area_id and a.scheme_code = 'us.nyc_ed'
                       join area_parent p on p.area_id = c.area_id and p.valid @> %s::date
                       join area_code pc on pc.area_id = p.parent_id
                       join reporting_unit u on u.scheme_code = 'us.county' and u.code = pc.code
                       where c.valid @> %s::date""",
                    [day, day],
                ).fetchall()
            }
            codes = sorted(frame["ed"].unique())
            missing = [code for code in codes if code not in places]
            if missing:
                raise ValueError(f"{day}: no election district areas for {missing[:10]}")
            units = {code: cs.unit("us.nyc_ed", code, day) for code in codes}
            cs.frame(
                "reporting_unit",
                pl.DataFrame(
                    {
                        "id": [str(units[code]) for code in codes],
                        "polity_id": str(city),
                        "scheme_code": "us.nyc_ed",
                        "code": codes,
                        "parent_id": [str(places[code][1]) for code in codes],
                        "area_relation": "equals",
                        "area_id": [str(places[code][0]) for code in codes],
                        "kind": "standard",
                        "valid_from": day,
                        "valid_to": day + timedelta(days=1),
                    }
                ),
            )
            cs.frame(
                "reporting_unit_area",
                pl.DataFrame(
                    {
                        "unit_id": [str(units[code]) for code in codes],
                        "area_id": str(cs.area("us.place", "3651000")),
                        "valid_from": day,
                        "valid_to": day + timedelta(days=1),
                    }
                ),
            )
            councils = frame.filter(pl.col("office") == "council").select("ed", "district").unique().sort("ed")
            if councils["ed"].is_duplicated().any():
                raise ValueError(f"{day}: an election district votes in two council districts")
            cs.frame(
                "reporting_unit_area",
                councils.select(
                    unit_id=pl.col("ed").replace_strict({code: str(unit) for code, unit in units.items()}),
                    area_id=pl.col("district").replace_strict(
                        {
                            district: str(cs.area("us.local_district", f"36:3651000:council:district-{district}"))
                            for district in councils["district"].unique()
                        }
                    ),
                    valid_from=pl.lit(day),
                    valid_to=pl.lit(day + timedelta(days=1)),
                ),
            )
            for (contest_key,) in frame.select("key").unique().sort("key").iter_rows():
                contest = cs.key("us.contest", contest_key)
                if contest in cs.minted:
                    raise ValueError(f"{contest_key} must already exist")
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=cs.key("us.election", f"{day}:new-york-city:primary"),
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=False,
                    completeness=1,
                )
                if result_set not in cs.minted:
                    continue
                rows = frame.filter(pl.col("key") == contest_key)
                recertified = RECERTIFIED.get((day, rows["page"][0]))
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"{METHOD} {EXACT}"
                    if recertified is None
                    else f"{METHOD} The Board certified this contest on {recertified[0]:%B %-d, %Y}, after it "
                    f"published the cast vote records, and its count differs slightly from them: summed over the "
                    f"contest's election districts, each candidate's total is within {recertified[1]} votes of the "
                    f"certified one in every round.{recertified[2]}",
                )
                labels = [label for label in rows["choice"].unique().sort() if label != INACTIVE]
                options = {label: cs.key("us.option", f"{contest_key}:{slug(label)}") for label in labels}
                if cs.minted & set(options.values()):
                    raise ValueError(f"{contest_key}: options missing from the official count")
                units_column = pl.col("ed").replace_strict({code: str(unit) for code, unit in units.items()})
                inactive = pl.col("choice") == INACTIVE
                cs.frame(
                    "result_value",
                    pl.concat(
                        [
                            rows.select(
                                result_set_id=pl.lit(str(result_set)),
                                unit_id=units_column,
                                option_id=pl.col("choice").replace_strict(
                                    {label: str(option) for label, option in options.items()}, default=None
                                ),
                                channel=pl.lit(0, pl.Int16),
                                measure=pl.when(inactive).then(pl.lit(21, pl.Int16)).otherwise(pl.lit(1, pl.Int16)),
                                count_round=pl.col("round").cast(pl.Int16),
                                flags=pl.lit(1, pl.Int16),
                                value=pl.col("votes").cast(pl.Int64),
                            ),
                            rows.filter(pl.col("round") == 1)
                            .group_by("ed")
                            .agg(pl.col("votes").sum())
                            .select(
                                result_set_id=pl.lit(str(result_set)),
                                unit_id=units_column,
                                option_id=pl.lit(None, pl.String),
                                channel=pl.lit(0, pl.Int16),
                                measure=pl.lit(4, pl.Int16),
                                count_round=pl.lit(1, pl.Int16),
                                flags=pl.lit(1, pl.Int16),
                                value=pl.col("votes").cast(pl.Int64),
                            ),
                        ]
                    ),
                )
        return cs.write()
