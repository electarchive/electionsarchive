from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import me_legislature, medsl
from ea.store import DATA, connect

SOURCE = "us.me_leg_xlsx"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RETURNS = "https://lldc.mainelegislature.org/Open/Elections/Returns/{year}/{day}/elec_{day}{letter}.xlsx"
FILES = {
    (date(2012, 11, 6), "house"): "t",
    (date(2012, 11, 6), "senate"): "v",
    (date(2014, 11, 4), "house"): "z",
    (date(2014, 11, 4), "senate"): "ab",
    (date(2016, 11, 8), "house"): "r",
    (date(2016, 11, 8), "senate"): "t",
}
PUBLISHER = "Maine Secretary of State"
TITLE = "General Election Tabulations"


def url(day: date, letter: str) -> str:
    return RETURNS.format(year=day.year, day=day.strftime("%Y%m%d"), letter=letter)


def fetch() -> None:
    for (day, _), letter in FILES.items():
        raw.fetch(SOURCE, url(day, letter), suffix=".xlsx")


def parse() -> None:
    rows = []
    checked: dict[tuple[date, str], list[int]] = {}
    for (day, chamber), letter in FILES.items():
        cells = pl.read_excel(
            raw.cached(SOURCE, url(day, letter)).path, engine="calamine", has_header=False, infer_schema_length=0
        ).rows()
        columns: list[tuple[int, str]] = []
        block: list[tuple[str, str, list[int]]] = []
        for row in [*cells, (None,) * 4]:
            values = [str(cell).strip() if cell is not None else None for cell in row]
            numeric = columns and (values[columns[0][0]] or "").replace(",", "").isdigit()
            header = values[0] in (None, "DIST") and any(
                value is not None and ("," in value or value.upper() == "BLANK") for value in values[3:]
            )
            if values[0] is None and (values[2] or "").upper().startswith("EMERGENCY") and block:
                values[0], values[2] = block[-1][0], "EMERGENCY"
            if values[0] is not None and values[0].isdigit():
                county = (
                    "ME:emergency"
                    if values[2] == "EMERGENCY"
                    else "ME:uocava"
                    if values[1] is None
                    else me_legislature.COUNTIES[values[1]]
                )
                block.append(
                    (values[0], county, [int((values[index] or "0").replace(",", "")) for index, _ in columns])
                )
                continue
            if not header and not numeric and values[0] is None and any(values[3:]):
                continue
            if (
                header
                and block
                and [(index, value) for index, value in enumerate(values) if index >= 3 and value not in (None, "%")]
                == columns
            ):
                continue
            if block and numeric:
                totals = [int(values[index].replace(",", "")) if values[index] else None for index, _ in columns]
                sums = [
                    sum(votes[index] for _, _, votes in block) if totals[index] is not None else None
                    for index in range(len(columns))
                ]
                if sums != totals:
                    raise ValueError(f"{day} {chamber} {block[0][0]}: {sums} against {totals}")
            if block:
                checked.setdefault((day, chamber), [0, 0])[0 if block and numeric else 1] += 1
            for district, county, votes in block:
                rows.extend(
                    (day, f"{chamber}:{int(district)}:g", county, name, count)
                    for (_, name), count in zip(columns, votes, strict=True)
                    if name.upper() not in ("BLANK", "TBC", "TOTAL VOTES CAST")
                )
            block = []
            if header:
                columns = [
                    (index, value) for index, value in enumerate(values) if index >= 3 and value not in (None, "%")
                ]
    print(checked)
    frame = (
        pl.DataFrame(
            rows,
            schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
            orient="row",
        )
        .group_by("day", "chamber", "code", "candidate", maintain_order=True)
        .agg(pl.col("votes").sum())
    )
    print(frame.group_by("day", office=pl.col("chamber").str.split(":").list.first()).agg(pl.col("chamber").n_unique()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("ME"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.when(pl.col("code").str.starts_with("ME:")).then(pl.lit("aggregate")).otherwise(pl.lit("county")),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes="votes",
        source=pl.lit("ME"),
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        held = [
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x join result_set rs on rs.contest_id = x.entity_id
                   join changeset c on c.id = rs.changeset_id
                   where x.scheme = 'us.contest' and x.value ~ '^20(12|14)-11-\\d\\d:ME:(house|senate):'
                     and c.pipeline = %s""",
                [me_legislature.SOURCE],
            ).fetchall()
        ]
        frame = frame.filter(~pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":ME:"), "chamber").is_in(held))
        print(f"{len(held)} contests already held by {me_legislature.SOURCE}")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2012 to 2016 State House and Senate generals by county from the Secretary of State's tabulations",
            polity="US-ME",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError("the tabulations source must already exist")
        for (day, _), letter in FILES.items():
            cs.artifact(raw.cached(SOURCE, url(day, letter)), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"ME": source},
            strict=True,
            authoritative="Source: the Secretary of State's tabulations.",
        )
        return cs.write()
