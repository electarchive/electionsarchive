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
VERSION = "1"
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
PUBLISHER = "Board of Elections in the City of New York"
METHOD = (
    "Tabulated by electionsarchive.com from the Board of Elections' cast vote records, election district by election "
    "district, following the official count's eliminations: skipped rankings are passed over, a ballot exhausts at its "
    "first overvote, a repeated candidate counts once, undeclared write-ins go out after the first round, and every "
    "later round eliminates the largest group of trailing candidates whose combined votes fall short of the next "
    "candidate's. Citywide, every round reproduces the Board's official ranked-choice rounds exactly. Each candidate's "
    "value at round n is their running total; an eliminated candidate has 0 at the round of their elimination."
)


def fetch() -> None:
    for link in CVRS.values():
        raw.fetch(SOURCE, link, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for day, link in CVRS.items():
        archive = zipfile.ZipFile(raw.cached(SOURCE, link).path)
        names = archive.namelist()
        labels = dict(
            fastexcel.read_excel(
                archive.read(next(name for name in names if name.endswith("CandidacyID_To_Name.xlsx")))
            )
            .load_sheet(0, dtypes="string")
            .to_polars()
            .iter_rows()
        )
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
            marks = ballots.filter(pl.col("page") == page).sort("ballot", "rank")
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
            continuing = set(ranked["choice"].unique())
            number = 1
            while True:
                top = (
                    ranked.filter(pl.col("choice").is_in(list(continuing)))
                    .group_by("ballot", maintain_order=True)
                    .agg(pl.col("ed").first(), pl.col("choice").first())
                )
                inactive = counted.join(top, on="ballot", how="anti").with_columns(choice=pl.lit(INACTIVE))
                tallies.append(
                    pl.concat([top, inactive])
                    .group_by("ed", "choice")
                    .agg(votes=pl.len())
                    .with_columns(date=pl.lit(day), page=pl.lit(page), round=pl.lit(number))
                )
                if len(continuing) <= 2:
                    break
                totals = dict(top.group_by("choice").len().iter_rows())
                order = sorted(continuing, key=lambda choice: totals.get(choice, 0))
                votes = [totals.get(choice, 0) for choice in order]
                if number == 1 and WRITE_INS in continuing:
                    out = {WRITE_INS}
                else:
                    batch = max(
                        (size for size in range(1, len(order) - 1) if sum(votes[:size]) < votes[size]), default=1
                    )
                    if batch == 1 and votes[0] == votes[1]:
                        raise ValueError(f"{day} {page}: tie for last in round {number}: {order[:2]}")
                    out = set(order[:batch])
                eliminated = (
                    pl.concat([top, inactive])
                    .filter(pl.col("choice").is_in(list(out)))
                    .select("ed", "choice")
                    .unique()
                    .with_columns(votes=pl.lit(0, pl.UInt32), date=pl.lit(day), page=pl.lit(page))
                )
                continuing -= out
                number += 1
                tallies.append(eliminated.with_columns(round=pl.lit(number)))
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
    ).filter(pl.col("votes").fill_null(0) != pl.col("votes_official").fill_null(0))
    if differences.height:
        raise ValueError(
            f"the CVR tabulation differs from the official rounds:\n{differences.sort('date', 'page', 'round')}"
        )
    tally.write_parquet(PARSED / "rounds.parquet")


def emit() -> Path:
    tally = pl.read_parquet(PARSED / "rounds.parquet").join(
        pl.read_parquet(DATA / "parsed" / "us.nyc_boe" / "rounds.parquet").select("date", "page", "key").unique(),
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
                cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
                rows = frame.filter(pl.col("key") == contest_key)
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
