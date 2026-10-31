import re
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.sc_enr_pri"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://www.enr-scvotes.org/SC/"
ELECTIONS = {
    date(2010, 6, 8): "16117/28318",
    date(2010, 6, 22): "16213/29430",
    date(2014, 6, 10): "51763/133986",
}
PARTIES = {"REP": "republican", "DEM": "democratic"}
PUBLISHER = "South Carolina State Election Commission"
TITLE = "Election results"


def address(path: str) -> str:
    return f"{SITE}{path}/reports/detailxml.zip"


def fetch() -> None:
    for path in ELECTIONS.values():
        try:
            raw.cached(SOURCE, address(path))
        except LookupError:
            raw.fetch(SOURCE, address(path), suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    rows = []
    for day, path in ELECTIONS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, address(path)).path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        for contest in root.iter("Contest"):
            found = re.fullmatch(
                r"U\.S\. (?:Senate|House of Representatives District (\d+))( \(Unexpired Term\))? ?- ?(REP|DEM)",
                contest.get("text", ""),
            )
            if found is None:
                continue
            district = int(found[1]) if found[1] else None
            for choice in contest.iter("Choice"):
                for county in [*choice.iter("County"), *choice.iter("Precinct")]:
                    rows.append(
                        {
                            "day": day,
                            "chamber": f"{'house' if district else 'senate'}:{'special:' if found[2] else ''}{PARTIES[found[3]]}",
                            "district": district,
                            "candidate": re.sub(r"\s+", " ", choice.get("text", "")).strip().upper(),
                            "code": counties[re.sub(r"[^a-z]", "", county.get("name", "").lower())],
                            "votes": int(county.get("votes", "0")),
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = (
        pl.DataFrame(rows, schema_overrides={"district": pl.Int16})
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("SC"),
            writein=pl.lit(False),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("SC"),
        )
    )
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("day", "chamber", "district")
        .agg(pl.col("votes").sum(), pl.col("code").n_unique())
        .sort("day", "chamber", "district")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's 2010 U.S. House Republican runoffs by county",
            polity="US-SC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        for path in ELECTIONS.values():
            cs.artifact(raw.cached(SOURCE, address(path)), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").filter(pl.col("day") == date(2010, 6, 22)),
            {"SC": source},
            strict=True,
        )
        return cs.write()
