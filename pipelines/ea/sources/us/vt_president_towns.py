import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl
from ea.store import connect

SOURCE = "us.vt_president_towns"
VERSION = "1"
TENANT, PUBLISHER, SITE = elstats.TENANTS["VT"]
YEAR = 1976
NOTE = (
    "The Vermont Secretary of State's election archive prints the returns of all 246 towns for the 1976 presidential "
    "election, summing to Ford 100,048, Carter 77,982, McCarthy 3,946, Camejo 413, and LaRouche 163 (182,552 votes). "
    "The Clerk of the House's statistics (Ford 100,387 and Carter 77,798) and Wikipedia (Ford 102,085 and Carter "
    "81,044) differ, so the state's own canvass is the default and those sets are kept beside it."
)


def emit() -> Path:
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "VT")
    towns: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        towns.setdefault(elstats.plain(name), geoid)
        towns.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid)
    towns["sherburne"] = towns["killington"]
    archive_csv = raw.cached(elstats.SOURCE, elstats.url(TENANT, YEAR))
    rows = (
        pl.read_csv(archive_csv.path, infer_schema=False)
        .filter(
            pl.col("election_date").str.starts_with(str(YEAR)),
            pl.col("election_date").str.slice(5, 2) == "11",
            pl.col("office_name") == "President",
            pl.col("division_type") == "City/Town",
        )
        .with_columns(
            code=pl.col("division_name")
            .map_elements(elstats.plain, return_dtype=pl.String)
            .replace_strict(towns, default=None),
            candidate=pl.col("candidate_name").fill_null("").str.to_uppercase().str.strip_chars(),
            votes=pl.col("votes").cast(pl.Int64, strict=False),
        )
    )
    lost = rows.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched towns: {lost['division_name'].unique().to_list()}")
    frame = (
        rows.with_columns(
            measure=pl.col("candidate").replace_strict(
                {**medsl.PSEUDO, "BLANK/VOID/SCATTERING": 0}, default=1, return_dtype=pl.Int16
            ),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE|ALL OTHER"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            code=pl.when(pl.col("code") == "5000710675").then(pl.lit("5010675")).otherwise("code"),
            year=pl.lit(YEAR, pl.Int32),
            chamber=pl.lit("president"),
            day=pl.lit(date(YEAR, 11, 2)),
            special=pl.lit(False),
            state=pl.lit("VT"),
            district=pl.lit(None, pl.Int16),
            kind=pl.when(pl.col("code") == "5000710675").then(pl.lit("place")).otherwise(pl.lit("town")),
            source=pl.lit("VT"),
        )
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Vermont's 1976 presidential election by town from the Secretary of State's archive, as the default",
            polity="US",
        )
        sources = {"VT": cs.source(PUBLISHER, elstats.TITLE)}
        cs.add(
            "source",
            id=sources["VT"],
            kind="official_body",
            publisher=PUBLISHER,
            title=elstats.TITLE,
            url=SITE,
            license="Public record",
        )
        cs.artifact(archive_csv, sources["VT"])
        medsl.attach(cs, conn, frame, sources, grain="town")
        previous: dict[str, tuple] = {}
        for row in conn.execute(
            """select r.id, r.election_id, r.contest_id, r.stage, r.kind, r.source_id, r.completeness, s.publisher,
                      r.is_default, r.status
               from result_set r join contest k on k.id = r.contest_id join source s on s.id = r.source_id
               join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
               where x.value = '1976-11-02:VT:president' and r.status = 'active'"""
        ).fetchall():
            previous[row[7]] = row
        clerk = previous["Clerk of the House of Representatives"]
        county = previous["Vermont Secretary of State"]
        for row, default in ((clerk, False), (county, True)):
            cs.add(
                "result_set",
                id=row[0],
                election_id=row[1],
                contest_id=row[2],
                stage=row[3],
                kind=row[4],
                source_id=row[5],
                status="active",
                is_default=default,
                completeness=row[6],
            )
        cs.add("note", entity_id=county[0], kind="methodology", body_md=NOTE)
        cs.add("note", entity_id=clerk[0], kind="methodology", body_md=NOTE)
        return cs.write()
