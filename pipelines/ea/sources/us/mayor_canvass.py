import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mayor_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PHILADELPHIA = "https://vote.phila.gov/files/"
FILES = {
    date(2007, 11, 6): ("election-results/2007_General_WD.csv", "election-results/2007_Primary_WD.csv"),
    date(2011, 11, 8): ("election-results/2011_General_WD.csv", "election-results/2011_Primary_WD.csv"),
    date(2015, 11, 3): ("election-results/2015_GENERAL/2015_GENERAL_-_WARD_RESULTS_-_PUBLIC.xlsx", None),
    date(2019, 11, 5): ("raw-data/2019_general.csv", None),
}
PARTIES = {
    "D": ("democratic-party", "Democratic"),
    "DEM": ("democratic-party", "Democratic"),
    "DEMOCRATIC": ("democratic-party", "Democratic"),
    "R": ("republican-party", "Republican"),
    "REP": ("republican-party", "Republican"),
    "REPUBLICAN": ("republican-party", "Republican"),
    "SOCIALIST WORKERS PARTY": ("socialist-workers-party", "Socialist Workers"),
}
PUBLISHER = "Philadelphia City Commissioners"
TITLE = "Election results by ward and division"
NOTE = (
    "Citywide totals summed from the City Commissioners' division results. Party labels are as printed in the "
    "results file; for 2007 and 2011, whose general election files print no party, the Democratic and Republican "
    "nominees' parties come from the same year's primary results file, and other candidates carry none."
)


def fetch() -> None:
    for files in FILES.values():
        for name in files:
            if name is not None:
                raw.fetch(SOURCE, PHILADELPHIA + name, suffix=Path(name).suffix)


def parse() -> None:
    rows = []
    for day, (general, primary) in FILES.items():
        path = raw.cached(SOURCE, PHILADELPHIA + general).path
        if general.endswith(".xlsx"):
            frame = (
                fastexcel.read_excel(path)
                .load_sheet("Precint-level Data")
                .to_polars()
                .filter(pl.col("RACE") == "MAYOR")
                .select(candidate="CANDIDATE", party="PARTY", votes=pl.col("VOTES").cast(pl.Int64))
            )
        elif primary is None:
            frame = (
                pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy")
                .filter(pl.col("CATEGORY") == "MAYOR")
                .select(candidate="SELECTION", party="PARTY", votes=pl.col("VOTE COUNT").cast(pl.Int64))
            )
        else:
            nominees = {
                " ".join(found[1].split()): found[2]
                for column in pl.read_csv(
                    raw.cached(SOURCE, PHILADELPHIA + primary).path, n_rows=0, infer_schema_length=0
                ).columns
                if (found := re.fullmatch(r"(.+) - MAYOR-(D|R|DEM|REP)", column)) is not None
            }
            wide = pl.read_csv(path, infer_schema_length=0)
            frame = pl.DataFrame(
                [
                    (name, nominees.get(name), wide[column].cast(pl.Int64).sum())
                    for column in wide.columns
                    if column.endswith(" - MAYOR")
                    for name in [" ".join(column.removesuffix(" - MAYOR").split())]
                ],
                schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
                orient="row",
            )
        rows.append(
            frame.group_by("candidate", "party")
            .agg(pl.col("votes").sum())
            .with_columns(day=pl.lit(day), write_in=pl.col("candidate") == "Write In")
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(rows).sort("day", "votes", descending=[False, True]).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Philadelphia's 2007, 2011, 2015, and 2019 mayoral generals from the City Commissioners' official results, with each candidate's declared party, superseding ALGED's partyless sets",
            polity="US-PA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://vote.phila.gov/resources-data/past-election-results/archived-data-sets/",
            license="Public record",
        )
        for files in FILES.values():
            for name in files:
                if name is not None:
                    cs.artifact(raw.cached(SOURCE, PHILADELPHIA + name), source)
        unit = cs.unit("us.place", "4260000")
        values = []
        for (day,), rows in candidates.group_by("day", maintain_order=True):
            contest_key = f"alged:4260000_{day.year}_11_philadelphia_PA_Mayor_mayor_1"
            contest = cs.key("us.contest", contest_key)
            if contest in cs.minted:
                raise ValueError(f"{contest_key} must already exist")
            columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
            prior = conn.execute(
                f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                    join changeset c on c.id = r.changeset_id
                    where r.contest_id = %s and c.pipeline = 'us.alged'""",
                [contest],
            ).fetchone()
            if prior is None:
                raise ValueError(f"{contest_key}: no ALGED result set")
            prior = dict(zip(columns, prior, strict=True))
            cs.add("result_set", **(prior | {"is_default": False}), status="superseded")
            roots = {
                re.sub(r"\W", "", label.split()[-1]).lower(): option
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=prior["election_id"],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            winner = rows.filter(~pl.col("write_in"))["candidate"][0]
            for candidate, party, votes, write_in in rows.select("candidate", "party", "votes", "write_in").iter_rows():
                if write_in:
                    option = cs.key("us.option", f"{contest_key}:write_in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    root = roots.get(re.sub(r"\W", "", candidate.split()[-1]).lower())
                    if root is None:
                        raise ValueError(f"{contest_key}: no ALGED option for {candidate}")
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=root,
                        kind="won" if candidate == winner else "lost",
                    )
                    option = root
                    if party in PARTIES:
                        key, label = PARTIES[party]
                        option = cs.key("us.option", f"{contest_key}:{slug(candidate)}:{key}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="ticket",
                            party_id=cs.key("us.party", key),
                            ballot_label=f"{candidate.title()} ({label})",
                        )
                values.append((str(result_set), str(unit), str(option), votes))
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
