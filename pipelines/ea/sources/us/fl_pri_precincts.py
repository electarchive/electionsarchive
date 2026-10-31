import re
import time
import unicodedata
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fl_precincts, medsl
from ea.store import DATA, connect

SOURCE = "us.fl_pri_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
BASE = "https://dos.fl.gov/media/"
FILES = {
    date(2016, 3, 15): "696297/precinctlevelelectionresults2016ppp.zip",
    date(2016, 8, 30): "697202/precinctlevelelectionresults2016pri.zip",
    date(2018, 8, 28): "700241/precinctlevelelectionresults2018pri.zip",
    date(2020, 3, 17): "703720/precinctlevelelectionresults2020ppp-corrected-12-2020.zip",
    date(2020, 8, 18): "703721/2020-pe-precinct-level-election-results-corrected-12-2020.zip",
    date(2022, 8, 23): "707057/enightprecinctfiles2022_pri.zip",
    date(2024, 3, 19): "708066/2024-pppoutputofficial-a.zip",
    date(2024, 8, 20): "708691/2024-pri-outputofficial.zip",
}
PARTIES = {"DEM": "democratic", "REP": "republican", "LPF": "libertarian"}


def fetch() -> None:
    for path in FILES.values():
        url = BASE + path
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            response = httpx.get(url, headers=HEADERS, timeout=300, follow_redirects=True)
            response.raise_for_status()
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".zip",
                retrieved_at=datetime.now(UTC),
                media_type="application/zip",
            )
            time.sleep(1)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '12%%'"""
            ).fetchall()
        }
    frames = []
    for day, path in FILES.items():
        parts: dict[str, dict[str, pl.DataFrame]] = {}
        with zipfile.ZipFile(raw.cached(SOURCE, BASE + path).path) as archive:
            for member in archive.namelist():
                found = re.search(r"([A-Za-z]{3})_PctResults\d{8}(_2|_recount2?)?\.txt$", member)
                if found is None:
                    continue
                parts.setdefault(found[1].upper(), {})[found[2] or ""] = pl.read_csv(
                    archive.read(member),
                    separator="\t",
                    has_header=False,
                    new_columns=fl_precincts.COLUMNS,
                    infer_schema=False,
                    quote_char=None,
                    encoding="utf8-lossy",
                    truncate_ragged_lines=True,
                )
        for versions in parts.values():
            main = versions.get("_2", versions.get(""))
            recount = versions.get("_recount2", versions.get("_recount"))
            if main is None:
                continue
            if recount is not None:
                main = pl.concat(
                    [main.join(recount.select("office_code").unique(), on="office_code", how="anti"), recount]
                )
            frames.append(main.with_columns(day=pl.lit(day)))
    office = pl.col("office").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase()
    frame = (
        pl.concat(frames)
        .with_columns(
            office=office,
            district=pl.col("district").str.extract(r"(\d+)", 1).cast(pl.Int16),
            party=pl.col("party").str.strip_chars().str.to_uppercase(),
            code=pl.col("county")
            .str.to_lowercase()
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(counties, default=None),
            number=pl.col("number").fill_null("").str.replace_all("\x00", "").str.strip_chars(),
        )
        .with_columns(
            kind=pl.when(pl.col("office").str.contains(r"CONGRESS|CONG DIST|^U\.?S\.? REP"))
            .then(pl.lit("house"))
            .when(pl.col("office").str.contains(r"UNITED STATES SENATOR|\bU\.?S\.? SENATOR"))
            .then(pl.lit("senate"))
            .when(pl.col("office").str.contains("PRESIDENT") & ~pl.col("office").str.contains("VICE"))
            .then(pl.lit("president"))
        )
        .filter(
            pl.col("kind").is_not_null()
            & pl.col("votes").is_not_null()
            & (pl.col("place").fill_null("").str.to_lowercase() != "all precincts")
        )
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    frame = frame.with_columns(
        party=pl.col("party")
        .replace_strict(PARTIES, default=None)
        .drop_nulls()
        .first()
        .over("day", "county", "kind", "district", "office_code")
    ).filter(pl.col("party").is_not_null())
    frame = (
        frame.with_columns(
            candidate=pl.col("choice")
            .str.to_uppercase()
            .str.replace(r" ?/ ?.*$", "")
            .str.strip_chars()
            .replace({"NUMBER OF UNDER VOTES": "UNDERVOTES", "TIMES OVER VOTED": "OVERVOTES"}),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            county_code=pl.col("code"),
            code=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("FL"),
                "code",
                pl.col("county").str.to_uppercase(),
                "number",
                separator=":",
            ),
            chamber=pl.concat_str("kind", "party", separator=":"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter((pl.col("measure") > 0) & ~pl.col("candidate").str.contains(r"^(TIMES COUNTED|NUMBER OF PRECINCTS)"))
        .group_by("day", "chamber", "district", "code", "county_code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("FL"),
            kind=pl.lit("precinct"),
            source=pl.lit("FL"),
            district=pl.when(pl.col("chamber").str.starts_with("house")).then("district"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("year").agg(pl.col("code").n_unique()).rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's presidential, U.S. Senate, and U.S. House primaries by precinct, 2016 to 2024",
            polity="US-FL",
        )
        source = cs.source(fl_precincts.PUBLISHER, fl_precincts.TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=fl_precincts.PUBLISHER,
                title=fl_precincts.TITLE,
                url=fl_precincts.LANDING,
                license="Public record",
            )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        cs.scope_units("us.precinct", "____-__-__:FL:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '12%%'"
            ).fetchall()
        }
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                     and coalesce(r.completeness, 1) >= 0.98
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   where x.scheme = 'us.contest' and x.value ~ '^20[12][0-9]-..-..:FL:(house|senate|president):'""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("FL"),
                pl.when(pl.col("district").is_not_null())
                .then(
                    pl.lit("house:")
                    + pl.col("district").cast(pl.String)
                    + pl.lit(":")
                    + pl.col("chamber").str.split(":").list.last()
                )
                .otherwise("chamber"),
                separator=":",
            ).is_in(list(filled))
        )
        units = (
            frame.group_by("code")
            .agg(pl.col("state").first(), pl.col("county_code").first(), pl.col("day").min())
            .sort("code")
        )
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county_code").replace_strict(
                    {code: str(unit) for code, (unit, _) in counties.items()}
                ),
                area_relation=pl.lit("within"),
                area_id=pl.col("county_code").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        medsl.attach(cs, conn, frame.drop("county_code"), {"FL": source}, grain="precinct")
        return cs.write()
