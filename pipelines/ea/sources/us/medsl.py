import time
from datetime import date, timedelta
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.county_changes import ct_regions
from ea.store import DATA, Connection, connect

SOURCE = "us.medsl"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile/"
FILES = {
    "president": "13887042",
    "county": "13573089",
    "house": "13592823",
    "senate": "13887039",
}
PRECINCTS = {
    ("senate", 2016): ("3345325", "NLTQAD"),
    ("house", 2016): ("6412765", "PSKDUJ"),
    ("senate", 2018): ("6692658", "DGNAFS"),
    ("house", 2018): ("6692624", "IVIXLK"),
    ("senate", 2020): ("6100391", "ER9XTV"),
    ("house", 2020): ("13582893", "VLGF2M"),
    ("senate", 2022): ("13996913", "IAD3XR"),
    ("house", 2022): ("13996912", "EOKNGW"),
    ("senate", 2024): ("13731184", "ZCM3BN"),
    ("house", 2024): ("13731101", "USBYR4"),
}
PSEUDO = {
    **dict.fromkeys(
        [
            "UNDERVOTES",
            "UNDERVOTE",
            "UNDER VOTES",
            "BLANK",
            "BLANKS",
            "BLANK BALLOTS",
            "BLANK VOTES",
            "TIMES BLANK VOTED",
        ],
        6,
    ),
    **dict.fromkeys(
        [
            "OVERVOTES",
            "OVERVOTE",
            "OVER VOTES",
            "OVER",
            "VOID",
            "VOIDS",
            "BLANK/VOID",
            "REJECTED",
            "SPOILED",
            "NOT QUALIFIED",
            "REJECTED WRITE-INS",
            "INVALID WRITE-IN",
            "FICTITIOUS ENTRY",
            "UNQUALIFIED WRITE-IN",
            "UNQUALIFIED WRITE-INS",
            "UNQUALIFIED WRITEIN",
        ],
        5,
    ),
    **dict.fromkeys(
        [
            "CAST VOTES",
            "BALLOTS CAST",
            "TOTAL",
            "TOTAL VOTES",
            "TOTAL VOTES CAST",
            "CONTEST TOTALS",
            "CONTEST TOTAL",
            "TOTAL BALLOTS CAST",
            "UNDERVOTES-VOIDS",
            "REGISTERED VOTERS",
            "SPECIAL VOTES",
            "FEDERAL BALLOTS",
            "FEDERAL VOTES",
            "UNDER/OVER VOTES",
            "PUBLIC COUNTER",
            "ABSENTEE / MILITARY",
            "ABSENTEE/MILITARY",
            "AFFIDAVIT",
            "FEDERAL",
            "EMERGENCY",
            "MANUALLY COUNTED EMERGENCY",
            "SPECIAL PRESIDENTIAL",
        ],
        0,
    ),
}
POOLED = {
    "",
    "WRITEIN",
    "WRITE-IN",
    "[WRITE-IN]",
    "WRITE IN",
    "WRITE-INS",
    "WRITE IN VOTES",
    "WRITE-IN TOTALS",
    "SCATTERING",
    "SCATTER",
    "SCATTERED VOTES",
    "OTHER WRITE-INS",
    "ALL OTHERS",
    "OTHERS",
    "UNRESOLVED WRITE-IN",
    "UNASSIGNED WRITE-INS",
    "PERSONAL CHOICE",
    "NOT ASSIGNED",
}
MISNAMED = {"WENDY ROSS": "WENDY LONG", "ROBIN TURNER": "ROBIN WILSON", "ROBIN TAYLOR": "ROBIN WILSON"}
GENERIC = {"Scattered", "All Others", "Miscellaneous", "Other"}
LOCATOR = ("page_id", "x0", "y0", "x1", "y1")
RECODED = {"46113": "46102", "36000": "2938000", "29380": "2938000", "36122": "36123"}
AGGREGATES = {
    "STATEWIDE WRITEIN": "write-in",
    "MAINE UOCAVA": "uocava",
    "FEDERAL PRECINCT": "federal",
    "DISTRICT 99": "99",
}
MEASURES = {"TOTAL VOTES CAST": 3, "UNDERVOTES": 6, "OVERVOTES": 5, "SPOILED": 5}
NAMED = {(2020, "GREEN"): "HOWIE HAWKINS", (2020, "GREEN", "AK"): "JESSE VENTURA"}
BEDFORD = date(2013, 7, 1)


def fetch() -> None:
    for file in [*FILES.values(), *(file for file, _ in PRECINCTS.values())]:
        try:
            raw.cached(SOURCE, DATAVERSE + file)
        except LookupError:
            raw.fetch(SOURCE, DATAVERSE + file, suffix=".tab")
            time.sleep(1)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.read_csv(raw.cached(SOURCE, DATAVERSE + FILES["county"]).path, separator="\t", infer_schema_length=0)
        .filter(pl.col("office") == "US PRESIDENT")
        .with_columns(
            year=pl.col("year").cast(pl.Int64),
            votes=pl.col("candidatevotes").cast(pl.Int64),
            totalvotes=pl.col("totalvotes").cast(pl.Int64),
            kind=pl.when(pl.col("county_name").is_in(list(AGGREGATES)))
            .then(pl.lit("aggregate"))
            .when(pl.col("state_po") == "AK")
            .then(pl.lit("district"))
            .otherwise(pl.lit("county")),
        )
        .with_columns(
            code=pl.when(pl.col("kind") == "aggregate")
            .then(pl.col("state_po") + ":" + pl.col("county_name").replace_strict(AGGREGATES, default=None))
            .when(pl.col("kind") == "district")
            .then(pl.col("year").cast(pl.String) + ":" + pl.col("county_fips").str.slice(-2))
            .otherwise(pl.col("county_fips").str.zfill(5).replace(RECODED)),
            measure=pl.col("candidate").replace_strict(MEASURES, default=1, return_dtype=pl.Int16),
            candidate=pl.struct("year", "party", "state_po", "candidate").map_elements(
                lambda row: (
                    NAMED.get(
                        (row["year"], row["party"], row["state_po"]),
                        NAMED.get((row["year"], row["party"]), row["candidate"]),
                    )
                    if row["candidate"] == "OTHER"
                    else row["candidate"]
                ),
                return_dtype=pl.String,
            ),
        )
        .with_columns(
            kind=pl.when((pl.col("kind") == "county") & (pl.col("code").str.len_chars() > 5))
            .then(pl.lit("place"))
            .otherwise("kind")
        )
        .filter(~((pl.col("code") == "51515") & (pl.col("year") > BEDFORD.year)))
        .filter(~((pl.col("year") == 2004) & (pl.col("state_po") == "AK")))
    )
    totaled = frame.group_by("year", "state_po", "code").agg(totaled=(pl.col("mode") == "TOTAL").any())
    counted = (
        frame.join(totaled, on=["year", "state_po", "code"])
        .filter(~pl.col("totaled") | (pl.col("mode") == "TOTAL"))
        .with_columns(candidate=pl.when(pl.col("measure") == 1).then("candidate"))
        .group_by("year", "state_po", "code", "kind", "candidate", "measure", maintain_order=True)
        .agg(pl.col("votes").sum(), pl.col("totalvotes").max())
    )
    uneven = (
        counted.filter(pl.col("measure") == 1)
        .group_by("year", "state_po", "code")
        .agg(pl.col("votes").sum(), pl.col("totalvotes").max())
        .filter(pl.col("votes") != pl.col("totalvotes"))
    )
    if uneven.height:
        print(f"{uneven.height} units whose candidate votes differ from MEDSL's totalvotes")
        print(uneven.group_by("year", "state_po").len().sort("year", "state_po"))
    counted.drop("totalvotes").rename({"state_po": "state"}).write_parquet(PARSED / "county.parquet")

    frame = precincts({key: raw.cached(SOURCE, DATAVERSE + file).path for key, (file, _) in PRECINCTS.items()})
    frame.write_parquet(PARSED / "precincts.parquet")
    frame = (
        frame.with_columns(
            code=pl.when((pl.col("state") == "CT") & (pl.col("year") >= 2022) & (pl.col("kind") == "county"))
            .then(
                pl.col("jurisdiction")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(ct_regions(), default=None)
            )
            .otherwise("code")
        )
        .group_by(
            "year",
            "chamber",
            "day",
            "special",
            "state",
            "district",
            "kind",
            "code",
            "candidate",
            "measure",
            maintain_order=True,
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
    )
    print(frame.filter(pl.col("kind") == "aggregate").group_by("code").agg(pl.col("votes").sum()).sort("code"))
    if frame["code"].null_count():
        raise ValueError(f"{frame['code'].null_count()} rows have no unit")
    frame.write_parquet(PARSED / "congress.parquet")


def precincts(files: dict[tuple[str, int], Path]) -> pl.DataFrame:
    frames = []
    for (chamber, year), path in files.items():
        frame = pl.scan_csv(
            path,
            separator="\t" if "\t" in path.open(errors="replace").readline() else ",",
            infer_schema_length=0,
            encoding="utf8-lossy",
        )
        if year == 2016:
            frame = frame.rename(
                {"state_postal": "state_po", "jurisdiction": "jurisdiction_name", "party": "party_detailed"},
                strict=False,
            ).with_columns(date=pl.lit("2016-11-08"))
        fips = pl.col("county_fips").str.replace(r"\.0$", "")
        frames.append(
            frame.with_columns(pl.col("precinct", "mode").fill_null(""))
            .filter(
                ~pl.col("precinct").str.to_uppercase().str.contains(r"TOTALS$|^COUNTY TOTAL$|- TOTAL$")
                & ~((pl.col("precinct").str.to_uppercase() == "TOTAL") & (pl.col("mode").str.to_uppercase() == "TOTAL"))
            )
            .filter(
                pl.col("office")
                .str.to_uppercase()
                .str.contains(
                    {
                        "senate": "SENATE",
                        "president": "^US PRESIDENT$",
                        "state": "^STATE (HOUSE|SENATE)$",
                        "statewide": r"GOVERNOR|ATTORNEY GENERAL|SECRETARY OF|TREASURER|AUDITOR|COMPTROLLER|CONTROLLER|"
                        r"CHIEF FINANCIAL|SUPERINTENDENT|COMMISSIONER|SUPREME|APPEALS|BOARD OF EDUCATION|REGENT|"
                        r"PUBLIC SERVICE|PUBLIC UTILITIES|MINE INSPECTOR|TRUSTEE",
                        "retention": r"JUDGE|JUSTICE|COURT|CRT|APPEAL|APPELLATE|RETAIN|RETENTION",
                    }.get(chamber, r"U\.?S\.? HOUSE|HOUSE OF REP")
                )
                & (
                    (pl.col("state_po") != "DC")
                    | pl.col("office").str.to_uppercase().str.contains("DELEGATE")
                    | (year < 2018)
                    | (chamber in ("president", "statewide", "retention"))
                )
            )
            .select(
                year=pl.lit(year),
                chamber=pl.lit(chamber),
                day=pl.col("date").replace({"11/8/22": "2022-11-08"}).str.to_date(),
                special=pl.col("special").str.to_uppercase() == "TRUE",
                state="state_po",
                district=pl.col("district").str.replace(r"\.0$", "").cast(pl.Int16, strict=False)
                if chamber == "house"
                else pl.lit(None, pl.Int16),
                fips=pl.when(fips.str.contains(r"^\d{4,5}$") & ~fips.is_in(["23000", "23099"])).then(
                    fips.str.zfill(5).replace(RECODED)
                ),
                jurisdiction=pl.col("jurisdiction_name").fill_null("").str.to_uppercase(),
                precinct=pl.col("precinct").fill_null(""),
                candidate=pl.col("candidate")
                .fill_null("")
                .str.to_uppercase()
                .str.strip_chars()
                .str.strip_prefix("WRITE-IN: "),
                writein=pl.col("writein").str.to_uppercase() == "TRUE",
                mode=pl.col("mode").fill_null("").str.to_uppercase(),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                office=pl.col("office").str.to_uppercase()
                if chamber in ("state", "statewide", "retention")
                else pl.lit(None, pl.String),
                seat=pl.col("district") if chamber in ("state", "statewide", "retention") else pl.lit(None, pl.String),
                party=pl.col("party_detailed")
                if chamber in ("state", "statewide", "retention")
                else pl.lit(None, pl.String),
                stage=pl.col("stage").str.to_uppercase()
                if chamber in ("state", "statewide", "retention")
                else pl.lit(None, pl.String),
                magnitude=pl.col("magnitude").cast(pl.String)
                if chamber == "statewide" and year > 2016
                else pl.lit(None, pl.String),
            )
        )
    frame = (
        pl.concat(frames)
        .collect()
        .filter(pl.col("votes").is_not_null())
        .with_columns(
            measure=pl.col("candidate").replace_strict(PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("writein") | pl.col("candidate").is_in(POOLED),
            candidate=pl.when(pl.col("candidate").is_in(POOLED))
            .then(pl.lit("WRITE-INS"))
            .when((pl.col("year") == 2016) & (pl.col("state") == "NY"))
            .then(pl.col("candidate").replace(MISNAMED))
            .otherwise("candidate"),
        )
        .filter(pl.col("measure") > 0)
    )
    towns = (
        frame.filter(pl.col("fips").is_not_null() & pl.col("state").is_in(["NH", "ME"]))
        .group_by("state", "jurisdiction")
        .agg(pl.col("fips").unique())
        .filter(pl.col("fips").list.len() == 1)
        .select("state", "jurisdiction", town=pl.col("fips").list.first())
    )
    known = frame.filter(pl.col("district").is_not_null() & (pl.col("measure") == 1) & ~pl.col("writein"))
    named = (
        known.group_by("year", "state", "candidate")
        .agg(pl.col("district").unique())
        .filter(pl.col("district").list.len() == 1)
        .select("year", "state", "candidate", named=pl.col("district").list.first())
    )
    single = (
        known.group_by("year", "state")
        .agg(pl.col("district").unique())
        .filter(pl.col("district").list.len() == 1)
        .select("year", "state", single=pl.col("district").list.first())
    )
    precinct = ["year", "chamber", "day", "special", "state", "fips", "jurisdiction", "precinct"]
    frame = (
        frame.join(towns, on=["state", "jurisdiction"], how="left")
        .join(named, on=["year", "state", "candidate"], how="left")
        .join(single, on=["year", "state"], how="left")
        .with_columns(
            fips=pl.coalesce("fips", "town"),
            district=pl.when(pl.col("chamber") == "house").then(
                pl.coalesce(
                    "district",
                    "single",
                    pl.when(pl.col("named").drop_nulls().n_unique().over(precinct) == 1).then(
                        pl.col("named").drop_nulls().first().over(precinct)
                    ),
                    pl.when(pl.col("district").is_null().all().over("year", "chamber", "state")).then(
                        pl.lit(0, pl.Int16)
                    ),
                )
            ),
            ak=pl.coalesce(
                pl.col("jurisdiction").str.extract(r"DISTRICT (\d+)"),
                pl.col("precinct").str.to_uppercase().str.extract(r"^HD(\d+)"),
            ).cast(pl.Int16),
        )
    )
    orphaned = frame.filter((pl.col("chamber") == "house") & pl.col("district").is_null())
    if orphaned.height:
        print(f"{orphaned['votes'].sum()} House votes in precincts with no inferable district")
        print(orphaned.group_by("year", "state").agg(pl.col("votes").sum()).sort("year", "state"))
    frame = (
        frame.filter((pl.col("chamber") != "house") | pl.col("district").is_not_null())
        .with_columns(
            kind=pl.when(pl.col("state") == "AK")
            .then(pl.when(pl.col("ak") == 99).then(pl.lit("aggregate")).otherwise(pl.lit("district")))
            .when(pl.col("fips").is_null())
            .then(pl.lit("aggregate"))
            .when(pl.col("fips").str.len_chars() > 5)
            .then(pl.lit("place"))
            .otherwise(pl.lit("county")),
        )
        .with_columns(
            code=pl.when(pl.col("kind") == "district")
            .then(pl.col("year").cast(pl.String) + ":" + pl.col("ak").cast(pl.String).str.zfill(2))
            .when(pl.col("kind") == "aggregate")
            .then(
                pl.col("state")
                + ":"
                + pl.when(pl.col("state") == "AK")
                .then(pl.lit("99"))
                .when(pl.col("state") == "RI")
                .then(pl.lit("federal"))
                .when(pl.col("state") == "GA")
                .then(pl.lit("floating"))
                .when(pl.col("jurisdiction").str.contains("UOCAVA"))
                .then(pl.lit("uocava"))
                .otherwise(pl.lit("unlocated"))
            )
            .otherwise("fips")
        )
        .filter(
            (pl.col("mode") == "TOTAL").any().over([*precinct, "district", "office", "seat", "candidate"]).not_()
            | (pl.col("mode") == "TOTAL")
        )
    )
    return frame


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "county.parquet")
    days = {year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in frame["year"].unique()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by county 2000–2024, and House and Senate results by county 2016–2024, from the MIT Election Data and Science Lab",
            polity="US",
        )
        source = cs.source("MIT Election Data and Science Lab", "County Presidential Election Returns 2000-2024")
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher="MIT Election Data and Science Lab",
            title="County Presidential Election Returns 2000-2024",
            url="https://doi.org/10.7910/DVN/VOQCHQ",
            license="CC0 1.0",
        )
        cs.artifact(raw.cached(SOURCE, DATAVERSE + FILES["county"]), source)
        states: dict[str, tuple[UUID, UUID, UUID]] = {
            iso.removeprefix("US-"): (polity, unit, area)
            for iso, polity, unit, area in conn.execute(
                """select p.iso_code, p.id, u.id, u.area_id from reporting_unit u
                   join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        cs.add(
            "scheme",
            code="us.ak_house_district",
            polity_id=cs.polity("US-AK"),
            kind="electoral_district",
            rank=3,
            noun="house district",
        )
        cs.add("scheme", code="us.place", polity_id=cs.polity("US"), kind="administrative", rank=3, noun="place")
        cs.add("scheme", code="us.aggregate", polity_id=cs.polity("US"), kind="synthetic", rank=3, noun="aggregate")
        units: dict[str, UUID] = {}
        for code, kind, state, year in (
            frame.group_by("code", "kind", "state").agg(pl.col("year").min()).sort("code").iter_rows()
        ):
            polity, parent, area = states[state]
            if kind == "county" and code != "51515":
                units[code] = cs.unit("us.county", code)
                continue
            scheme = {"district": "us.ak_house_district", "place": "us.place", "aggregate": "us.aggregate"}.get(
                kind, "us.county"
            )
            units[code] = cs.unit(scheme, code)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                parent_id=cs.unit("us.county", "29095") if code == "2938000" else parent,
                area_relation="equals" if kind == "district" else "within",
                area_id=cs.area("us.county", "51019")
                if code == "51515"
                else cs.area("us.county", "29095")
                if code == "2938000"
                else cs.area("us.sldl", f"AK:{int(code[-2:])}")
                if kind == "district"
                else area,
                kind="aggregate" if kind == "aggregate" else "standard",
                valid_from=days[year] if kind == "district" else None,
                valid_to=days[year] + timedelta(days=1) if kind == "district" else BEDFORD if code == "51515" else None,
            )
            if code == "2938000":
                cs.add(
                    "reporting_unit_area",
                    unit_id=units[code],
                    area_id=cs.area("us.place", code),
                    valid_from=None,
                    valid_to=None,
                    partial=True,
                )

        result_sets: dict[tuple[int, str], UUID] = {}
        options: dict[tuple[int, str, str], UUID] = {}
        for (year, state), lines in frame.group_by("year", "state", maintain_order=True):
            key = f"{days[year]}:{state}:president"
            contest = cs.key("us.contest", key)
            if contest in cs.minted:
                raise ValueError(f"{key}: the contest must already exist")
            election, official = conn.execute(
                """select c.election_id,
                          coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                   sum(v.value) filter (where v.measure = 1 and v.option_k <> 0))
                   from contest c
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k
                   where c.id = %s group by c.election_id""",
                [contest],
            ).fetchone() or (None, None)
            if election is None or official is None:
                raise ValueError(f"{key}: no official total to compare with")
            known = conn.execute(
                """select o.id, o.kind, o.ballot_label, n.text from contest_option o
                   left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   left join name n on n.entity_id = cy.person_id and n.is_preferred
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            for candidate, votes in (
                lines.filter(pl.col("measure") == 1)
                .group_by("candidate", maintain_order=True)
                .agg(pl.col("votes").sum())
                .iter_rows()
            ):
                if candidate == "OTHER":
                    options[(year, state, candidate)] = cs.key("us.option", f"{key}:others:medsl")
                    cs.add(
                        "contest_option",
                        id=options[(year, state, candidate)],
                        contest_id=contest,
                        kind="others",
                        ballot_label="Others",
                    )
                    continue
                words = {word for word in slug(candidate).split("-") if len(word) > 1 and word != "jr"}
                scores = sorted(
                    (
                        (
                            len(words & {word for text in texts if text for word in slug(text).split("-")}),
                            kind != "write_in",
                            option,
                        )
                        for option, kind, *texts in known
                    ),
                    key=lambda score: score[:2],
                    reverse=True,
                )
                if not scores or scores[0][0] == 0 or (len(scores) > 1 and scores[0][:2] == scores[1][:2]):
                    if not votes:
                        continue
                    raise ValueError(f"{key}: {candidate} matches no single option: {scores[:2]}")
                options[(year, state, candidate)] = scores[0][2]
            counted = lines.filter(pl.col("measure") == 1)["votes"].sum()
            previous = (
                conn.execute(
                    """select id, election_id, contest_id, stage, kind, source_id, is_default, as_of, completeness,
                              supersedes_id from result_set
                       where id = (select entity_id from external_id where scheme = %s and value = %s)""",
                    [f"{SOURCE}.result_set", f"{key}:county"],
                ).fetchone()
                if state == "AK"
                else None
            )
            if previous is not None:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "source_id",
                                "is_default",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            previous,
                            strict=True,
                        )
                    ),
                    status="superseded",
                )
            result_sets[(year, state)] = cs.key(
                f"{SOURCE}.result_set", f"{key}:county" + (":districts" if state == "AK" else "")
            )
            cs.add(
                "result_set",
                id=result_sets[(year, state)],
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(float(counted) / float(official), 4),
                supersedes_id=None if previous is None else previous[0],
            )

        minted = {pair for pair, result_set in result_sets.items() if result_set in cs.minted}
        rows = frame.filter(
            pl.struct("year", "state").map_elements(
                lambda row: (row["year"], row["state"]) in minted, return_dtype=pl.Boolean
            )
            & pl.col("votes").is_not_null()
        ).with_columns(
            option=pl.struct("year", "state", "candidate", "measure").map_elements(
                lambda row: (
                    str(options[(row["year"], row["state"], row["candidate"])])
                    if row["measure"] == 1 and (row["year"], row["state"], row["candidate"]) in options
                    else None
                ),
                return_dtype=pl.String,
            )
        )
        cs.frame(
            "result_value",
            rows.filter((pl.col("measure") != 1) | pl.col("option").is_not_null()).select(
                result_set_id=pl.struct("year", "state").map_elements(
                    lambda row: str(result_sets[(row["year"], row["state"])]), return_dtype=pl.String
                ),
                unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()}),
                option_id="option",
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure"),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.when(pl.col("kind") == "aggregate").then(pl.lit(32, pl.Int16)).otherwise(pl.lit(0, pl.Int16)),
                value="votes",
            ),
        )

        congress = pl.read_parquet(PARSED / "congress.parquet")
        sources: dict[tuple[str, int], UUID] = {}
        for (chamber, year), (file, doi) in PRECINCTS.items():
            title = (
                f"U.S. {'Senate' if chamber == 'senate' else 'House of Representatives'} Precinct-Level Returns {year}"
            )
            sources[(chamber, year)] = cs.source("MIT Election Data and Science Lab", title)
            cs.add(
                "source",
                id=sources[(chamber, year)],
                kind="academic",
                publisher="MIT Election Data and Science Lab",
                title=title,
                url=f"https://doi.org/10.7910/DVN/{doi}",
                license="CC0 1.0",
            )
            cs.artifact(raw.cached(SOURCE, DATAVERSE + file), sources[(chamber, year)])
        for code, kind, state, day in (
            congress.filter(pl.col("kind") != "county")
            .group_by("code", "kind", "state")
            .agg(pl.col("day").min())
            .iter_rows()
        ):
            if code in units:
                continue
            polity, parent, area = states[state]
            scheme = "us.ak_house_district" if kind == "district" else "us.aggregate"
            cs.add(
                "reporting_unit",
                id=cs.unit(scheme, code),
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                area_relation="equals" if kind == "district" else "within",
                area_id=cs.area("us.sldl", f"AK:{int(code[-2:])}") if kind == "district" else area,
                kind="standard" if kind == "district" else "aggregate",
                valid_from=day if kind == "district" else None,
                valid_to=day + timedelta(days=1) if kind == "district" else None,
            )

        attach(
            cs,
            conn,
            congress.with_columns(source=pl.col("chamber") + ":" + pl.col("year").cast(pl.String)),
            {f"{chamber}:{year}": source for (chamber, year), source in sources.items()},
        )
        return cs.write()


def attach(
    cs: Changeset,
    conn: Connection,
    congress: pl.DataFrame,
    sources: dict[str, UUID],
    redone: frozenset[str] = frozenset(),
    grain: str = "county",
    strict: bool = False,
    extend: bool = False,
    canvass: bool = False,
    partial: str | None = None,
) -> None:
    tag = str(cs.manifest["pipeline"])
    located = "page_id" in congress.columns
    states: dict[str, UUID] = {
        iso.removeprefix("US-"): polity
        for iso, polity in conn.execute("select iso_code, id from polity where iso_code like 'US-%%'").fetchall()
    }
    contests: dict[str, UUID] = dict(
        conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall()
    )
    counties: dict[str, tuple[UUID, UUID]] = {
        code: (unit, area)
        for code, unit, area in conn.execute(
            "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
        ).fetchall()
    }
    cs.add("scheme", code="us.county_part", polity_id=cs.polity("US"), kind="synthetic", rank=3, noun="county part")
    unmatched: list[tuple[str, str, int, int]] = []
    drifted: list[tuple[str, str, int, int]] = []
    taken: set[str] = set()
    parts: set[str] = set()
    values: list[pl.DataFrame] = []
    for (year, chamber, day, special, state, district), lines in congress.group_by(
        "year", "chamber", "day", "special", "state", "district", maintain_order=True
    ):
        base = (
            f"{day}:{state}-{district:02}:president"
            if chamber == "president" and district is not None
            else f"{day}:{state}:{chamber}"
            if district is None
            else f"{day}:{state}:house:{district}{chamber.removeprefix('house')}"
        )
        at_large = f"{day}:{state}:house:0{chamber.removeprefix('house')}"
        tried = [*([f"{base}:special"] if special else []), base]
        if chamber.startswith("house"):
            tried.append(at_large)
        key = next((k for k in tried if k in contests), None)
        if key is None or key in taken:
            print(
                f"{tried[0]}: {'no FEC contest' if key is None else 'already filled'}, {lines['votes'].sum()} skipped"
            )
            continue
        taken.add(key)
        contest = contests[key]
        election, district_area, first, official = conn.execute(
            """select c.election_id, c.district_area_id, v.count_round,
                      coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                               sum(v.value) filter (where v.measure = 1))::bigint
               from contest c join result_set rs on rs.contest_id = c.id and rs.is_default
               join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
               where c.id = %s group by 1, 2, 3 order by 3 limit 1""",
            [contest],
        ).fetchone() or (None, None, None, None)
        if election is None or first is None or not official or first > 1:
            print(f"{key}: no official total or first round, skipped")
            continue
        known = []
        for option, kind, label, name, fec in conn.execute(
            """select o.id, o.kind, o.ballot_label, n.text,
                          coalesce(
                            (select sum(v.value)::bigint from result_value v
                             where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1 and v.count_round = %s),
                            (select sum(v.value)::bigint from result_value v
                             join contest_option p on p.k = v.option_k and p.parent_option_id = o.id
                             where v.result_set_k = rs.k and v.measure = 1 and v.count_round = %s))
                   from contest_option o
                   join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   left join name n on n.entity_id = cy.person_id and n.is_preferred
                   where o.contest_id = %s and o.parent_option_id is null""",
            [first, first, contest],
        ).fetchall():
            tokens = [slug(text).split("-") for text in (label, name) if text]
            texts = {word for words in tokens for word in words} | {
                a + b for words in tokens for a, b in pairwise(words)
            }
            known.append((option, kind, label, texts, fec))
        picked: dict[str, str] = {}
        missing: list[tuple[str, str, int, int]] = []
        added: list[str] = []
        for candidate, writein, votes in (
            lines.filter(pl.col("measure") == 1)
            .group_by("candidate", maintain_order=True)
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .iter_rows()
        ):
            words = [
                word
                for word in slug(candidate).split("-")
                if len(word) > 1 and word not in {"jr", "sr", "ii", "iii", "iv", "junior", "senior"}
            ]
            if candidate != "WRITE-INS" and words:
                surname = words[0] if "," in candidate else words[-1]
                scores = sorted(
                    (
                        (
                            surname in texts or "".join(words[-2:]) in texts,
                            len(set(words) & texts),
                            kind != "write_in",
                            option,
                        )
                        for option, kind, _, texts, _ in known
                    ),
                    key=lambda score: score[:3],
                    reverse=True,
                )
                if scores and scores[0][0] and (len(scores) == 1 or scores[0][:3] != scores[1][:3]):
                    picked[candidate] = str(scores[0][3])
                    continue
                near = {
                    option
                    for option, kind, _, texts, _ in known
                    if kind != "write_in"
                    and any(
                        SequenceMatcher(None, surname, text).ratio() >= 0.8
                        or (
                            min(len(surname), len(text)) >= 3 and (text.startswith(surname) or surname.startswith(text))
                        )
                        for text in texts
                    )
                }
                valued = {
                    option
                    for option, kind, _, _, fec in known
                    if kind != "write_in" and fec and votes >= 1000 and abs(votes - fec) <= fec * 0.03
                }
                chosen = next((group for group in (near & valued, near, valued) if len(group) == 1), None)
                if chosen is not None:
                    picked[candidate] = str(chosen.pop())
                    continue
                if not writein:
                    if votes and extend:
                        added.append(candidate)
                        picked[candidate] = "pool"
                    elif votes:
                        missing.append((key, candidate, votes, official))
                    continue
            picked[candidate] = "pool"
        counted = lines.filter(pl.col("measure") == 1)
        tallies = (
            counted.with_columns(option=pl.col("candidate").replace_strict(picked, default=None))
            .group_by("option")
            .agg(pl.col("votes").sum())
        )
        overcounted = undercounted = held_back = False
        for option, kind, label, _, fec in known:
            medsl = int(tallies.filter(pl.col("option") == str(option))["votes"].sum())
            if kind in ("candidate", "ticket") and fec and abs(medsl - fec) > max(100, fec * 0.02):
                drifted.append((key, label, medsl, fec))
                held_back |= medsl < fec * 0.9
                overcounted |= medsl > fec
                undercounted |= medsl < fec
        if overcounted and not canvass:
            print(f"{key}: a candidate's county sum exceeds the FEC total, skipped")
            continue
        if undercounted and (strict or ":" in chamber) and (partial is None or held_back):
            print(f"{key}: a candidate's county sum falls short of the FEC total, skipped")
            continue
        pooled = tallies.with_columns(
            pl.col("option")
            .cast(pl.String)
            .replace(
                "pool",
                next(
                    (str(option) for option, kind, label, _, _ in known if kind == "write_in" and label in GENERIC),
                    "pool",
                ),
            )
        )
        if (
            max(known, key=lambda option: option[4] or 0)[0]
            != max(known, key=lambda option: int(pooled.filter(pl.col("option") == str(option[0]))["votes"].sum()))[0]
        ):
            print(f"{key}: the county sum puts another option first, skipped")
            columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
            for row in conn.execute(
                f"""select {", ".join(f"r.{column}" for column in columns)} from external_id x
                    join result_set r on r.id = x.entity_id and r.status = 'active'
                    where x.scheme = %s and x.value = %s""",
                [f"{tag}.result_set", f"{key}:{grain}"],
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), status="withdrawn")
            continue
        if any(votes > official * 0.01 for _, _, votes, official in missing):
            print(
                f"{key}: {max(missing, key=lambda line: line[2])[1]} has over 1 percent of the vote and matches no option, skipped"
            )
            continue
        if not canvass and counted.filter(pl.col("candidate").is_in(list(picked)))["votes"].sum() > official * 1.1:
            print(f"{key}: the county sum exceeds the official total by over 10 percent, skipped")
            continue
        if counted.filter(pl.col("candidate").is_in(list(picked)))["votes"].sum() < official * 0.5:
            print(f"{key}: the county sum covers under half the official total, skipped")
            continue
        unmatched.extend(missing)
        for candidate in added:
            person = cs.key(f"{tag}.person", f"{key}:{slug(candidate)}")
            option = cs.key("us.option", f"{key}:{person}")
            cs.add("person", id=person)
            cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
            cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate)
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{key}:{person}:head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                is_independent=False,
            )
            picked[candidate] = str(option)
        result_set = cs.key(f"{tag}.result_set", f"{key}:{grain}")
        previous = None
        if key in redone or (
            state == "CT"
            and year >= 2022
            and conn.execute(
                "select 1 from external_id where scheme = %s and value = %s", [f"{tag}.result_set", f"{key}:{grain}"]
            ).fetchone()
        ):
            previous, result_set = result_set, cs.key(f"{tag}.result_set", f"{key}:{grain}:2")
        if (
            result_set not in cs.minted
            and not conn.execute(
                """select 1 from result_set r where r.id = %s
                   and not exists (select from result_value v where v.result_set_k = r.k and v.value <> 0)""",
                [result_set],
            ).fetchone()
        ):
            continue
        if "pool" in picked.values():
            generic = [option for option, kind, label, _, _ in known if kind == "write_in" and label in GENERIC]
            pool = generic[0] if len(generic) == 1 else cs.key("us.option", f"{key}:write_in:{tag.removeprefix('us.')}")
            if pool in cs.minted:
                cs.add("contest_option", id=pool, contest_id=contest, kind="write_in", ballot_label="Write-ins")
            picked = {candidate: str(pool) if option == "pool" else option for candidate, option in picked.items()}
        if previous is not None:
            columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
            row = conn.execute(f"select {', '.join(columns)} from result_set where id = %s", [previous]).fetchone()
            if row is None:
                raise ValueError(f"{key}: no result set to supersede")
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="superseded")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=sources[lines["source"][0]],
            is_default=False,
            completeness=1
            if canvass
            else round(
                float(counted.filter(pl.col("candidate").is_in(list(picked)))["votes"].sum()) / float(official), 4
            ),
            supersedes_id=previous,
        )
        if undercounted and partial is not None:
            counted_votes = int(counted.filter(pl.col("candidate").is_in(list(picked)))["votes"].sum())
            cs.add(
                "note",
                entity_id=result_set,
                kind="caveat",
                body_md=f"The county totals add up to {counted_votes:,} votes against the official {int(official):,}: {partial}.",
            )
        members: dict[str, str] = {}
        for code, kind in lines.select("code", "kind").unique().iter_rows():
            if (
                kind in ("county", "place")
                and district is not None
                and (chamber == "president" or at_large not in contests)
            ):
                part = f"{year}:{code}:{district}" + (":special" if special else "")
                members[code] = str(cs.unit("us.county_part", part, on=day))
                if code not in counties:
                    raise ValueError(f"{key}: no county {code}")
                if members[code] in parts:
                    continue
                parts.add(members[code])
                cs.add(
                    "reporting_unit",
                    id=UUID(members[code]),
                    polity_id=states[state],
                    scheme_code="us.county_part",
                    code=part,
                    parent_id=counties[code][0],
                    area_relation="within",
                    area_id=counties[code][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                cs.add(
                    "reporting_unit_area",
                    unit_id=UUID(members[code]),
                    area_id=district_area,
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
            elif kind in ("county", "place"):
                if code not in counties:
                    raise ValueError(f"{key}: no county {code}")
                members[code] = str(counties[code][0])
            elif kind == "precinct":
                members[code] = str(cs.unit("us.precinct", code))
            else:
                members[code] = str(cs.unit("us.ak_house_district" if kind == "district" else "us.aggregate", code))
        values.append(
            lines.filter((pl.col("measure") != 1) | pl.col("candidate").is_in(list(picked)))
            .select(
                result_set_id=pl.lit(str(result_set)),
                unit_id=pl.col("code").replace_strict(members),
                option_id=pl.when(pl.col("measure") == 1).then(
                    pl.col("candidate").replace_strict(picked, default=None)
                ),
                channel=pl.col("channel") if "channel" in congress.columns else pl.lit(0, pl.Int16),
                measure=pl.col("measure"),
                count_round=pl.lit(first, pl.Int16),
                flags=pl.col("flags")
                if "flags" in congress.columns
                else pl.when(pl.col("kind") == "aggregate")
                .then(pl.lit(32, pl.Int16))
                .when(pl.lit(located) & pl.col("measure").eq(1))
                .then(pl.lit(8, pl.Int16))
                .otherwise(pl.lit(0, pl.Int16)),
                value="votes",
                **({name: pl.col(name) for name in LOCATOR} if located else {}),
            )
            .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
            .agg(
                pl.col("value").sum(),
                *(
                    [
                        pl.col("page_id").first(),
                        pl.col("page_id").n_unique().alias("pages"),
                        pl.col("x0").min(),
                        pl.col("y0").min(),
                        pl.col("x1").max(),
                        pl.col("y1").max(),
                    ]
                    if located
                    else []
                ),
            )
        )
    for key, label, medsl, fec in drifted:
        print(f"{key}: {label} {medsl} by county against {fec} from the FEC")
    for key, candidate, votes, official in unmatched:
        print(f"{key}: {candidate} ({votes} of {official}) matches no option, dropped")
    if values:
        everything = pl.concat(values)
        cs.frame("result_value", everything.select(pl.exclude("page_id", "pages", "x0", "y0", "x1", "y1")))
        if located:
            cs.frame(
                "value_locator",
                everything.filter((pl.col("pages") == 1) & (pl.col("measure") == 1)).select(
                    "result_set_id",
                    "unit_id",
                    "option_id",
                    "channel",
                    "measure",
                    "count_round",
                    "page_id",
                    source_id=pl.lit(None, pl.String),
                    x0=pl.col("x0"),
                    y0=pl.col("y0"),
                    x1=pl.col("x1"),
                    y1=pl.col("y1"),
                ),
            )
