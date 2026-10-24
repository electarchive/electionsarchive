import sys
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.gb import leh
from ea.store import connect

SOURCE = "gb.council_size"
VERSION = "1"
OCD_URL = "https://www.opencouncildata.co.uk/history2016-26.csv"
OCD_PAGE = "https://www.opencouncildata.co.uk/"
ALIASES = {
    "bristol": "bristol city of",
    "kingston upon hull": "kingston upon hull city of",
    "county durham": "durham",
    "buckinghamshire cc": "buckinghamshire",
    "city of london corporation": "city of london",
}
CAVEAT = (
    "Council size is the number of councillors, from Open Council Data UK's history table (CC BY-SA 4.0), a compiled "
    "source. Sizes are dated by calendar year, not by election day. The Local Government Boundary Commission for "
    "England's electoral data workbook was checked against it and not used: its council size lags reorganizations "
    "and disagrees with the official 2021 and 2022 results for several councils."
)


def fetch() -> None:
    raw.fetch(SOURCE, OCD_URL, suffix=".csv")


def emit() -> Path:
    lads: dict[str, str] = {}
    counties: dict[str, tuple[str, str]] = {}
    for year in leh.LOOKUPS:
        for row in leh.lookup(year).iter_rows(named=True):
            lads[row["la"]] = row["la_name"]
            if row["county"] is not None:
                counties[leh.fold(row["county_name"])] = (row["county"], row["county_name"])
    wards = pl.read_parquet(raw.parsed(leh.SOURCE) / "wards.parquet")
    councils = {code: lads[code] for code in wards["la_code"].unique().to_list()}
    for county in wards.filter(pl.col("ward_code").str.starts_with("E58"))["county"].unique().to_list():
        code, name = counties[leh.fold(county)]
        councils[code] = name

    def key(name: str) -> str:
        folded = leh.fold(name.removesuffix(" Council"))
        return ALIASES.get(folded, folded)

    by_name = {key(name): code for code, name in councils.items()}

    ocd = pl.read_csv(raw.cached(SOURCE, OCD_URL).path, infer_schema_length=0)
    sizes: dict[str, dict[int, int]] = {}
    for row in ocd.iter_rows(named=True):
        code = by_name.get(key(row["authority"]))
        if code is not None and row["total"]:
            sizes.setdefault(code, {})[int(row["year"])] = int(row["total"])

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Council sizes for English and Welsh councils, 2016 on, from Open Council Data UK",
            polity="GB",
        )
        compiler = cs.source("Open Council Data UK", "Council history 2016-2026")
        cs.add(
            "source",
            id=compiler,
            kind="compendium",
            publisher="Open Council Data UK",
            title="Council history 2016-2026",
            url=OCD_PAGE,
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Council history from Open Council Data UK, CC BY-SA 4.0.",
            notes=CAVEAT,
        )
        cs.artifact(raw.cached(SOURCE, OCD_URL), compiler)
        loaded = 0
        held = {row[0] for row in conn.execute("select value from external_id where scheme = 'gb.body'")}
        for code, series in sizes.items():
            if code not in held:
                continue
            body = cs.key("gb.body", code)
            years = sorted(series)
            runs = [year for index, year in enumerate(years) if index == 0 or series[year] != series[years[index - 1]]]
            for index, year in enumerate(runs):
                end = runs[index + 1] if index + 1 < len(runs) else None
                cs.add(
                    "body_size",
                    body_id=body,
                    seats=series[year],
                    valid_from=date(year, 1, 1),
                    valid_from_precision="year",
                    valid_to=date(end, 1, 1) if end is not None else None,
                    valid_to_precision="year",
                )
            cs.add("note", entity_id=body, kind="context", body_md=CAVEAT)
            loaded += 1
        print(f"{loaded} councils", file=sys.stderr)
        return cs.write()
