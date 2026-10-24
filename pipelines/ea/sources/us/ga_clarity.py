import os
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ga_clarity"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "precincts-ga"
PUBLISHER = "Georgia Secretary of State"
TITLE = "Election history: detailed results exports by county"
LANDING = "https://sos.ga.gov/page/election-results"
BASE = "https://sos.ga.gov/sites/default/files/2026-04/"
FILES = {
    date(2012, 11, 6): BASE + "november_6_2012_-_general_election.zip",
    date(2014, 11, 4): BASE + "november_4_2014_-_general_election.zip",
}
STATEWIDE = {
    "GOVERNOR": "governor",
    "LIEUTENANT GOVERNOR": "lieutenant-governor",
    "SECRETARY OF STATE": "secretary-of-state",
    "ATTORNEY GENERAL": "attorney-general",
    "STATE SCHOOL SUPERINTENDENT": "school-superintendent",
    "COMMISSIONER OF AGRICULTURE": "commissioner-of-agriculture",
    "COMMISSIONER OF INSURANCE": "commissioner-of-insurance",
    "COMMISSIONER OF LABOR": "commissioner-of-labor",
    "PRESIDENT OF THE UNITED STATES": "president",
}


def fetch() -> None:
    for url in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / url.rsplit("/", 1)[1]).read_bytes(),
                suffix=".zip",
                retrieved_at=datetime(2026, 10, 24, tzinfo=UTC),
                media_type="application/zip",
            )


def contest(text: str) -> tuple[str, int | None] | None:
    name = re.sub(r"\s+", " ", text.strip().upper())
    if name in STATEWIDE:
        return STATEWIDE[name], None
    if name.startswith("UNITED STATES SENATOR"):
        return "senate", None
    if matched := re.match(r"U\.S\. REPRESENTATIVE, DISTRICT (\d+)$", name):
        return "house", int(matched[1])
    if matched := re.match(r"STATE SENATOR, DISTRICT (\d+)$", name):
        return f"senate:{int(matched[1])}:g", None
    if matched := re.match(r"STATE REPRESENTATIVE, DISTRICT (\d+)$", name):
        return f"house:{int(matched[1])}:g", None
    if matched := re.match(r"PUBLIC SERVICE COMMISSION, DISTRICT (\d+)\b", name):
        return f"public-service-commissioner:district-{matched[1]}", None
    return None


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%%'"""
            ).fetchall()
        }
    records: list[tuple[date, str, str, str, str, int]] = []
    for day, url in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            for member in archive.namelist():
                if not member.endswith("-detailxml.zip"):
                    continue
                with zipfile.ZipFile(archive.open(member)) as inner:
                    root = ET.fromstring(inner.read("detail.xml"))
                county = re.sub(r"[^a-z]", "", (root.findtext("Region") or "").lower())
                for race in root.iter("Contest"):
                    if contest(race.get("text") or "") is None:
                        continue
                    for choice in race.iter("Choice"):
                        for channel in choice.iter("VoteType"):
                            for precinct in channel.iter("Precinct"):
                                records.append(
                                    (
                                        day,
                                        county,
                                        race.get("text") or "",
                                        precinct.get("name") or "",
                                        choice.get("text") or "",
                                        int(precinct.get("votes") or 0),
                                    )
                                )
    frame = pl.DataFrame(records, schema=["day", "county", "title", "place", "choice", "votes"], orient="row")
    titles = {title: contest(title) for title in frame["title"].unique().to_list()}
    frame = frame.with_columns(
        chamber=pl.col("title").replace_strict({t: c[0] for t, c in titles.items() if c}, default=None),
        district=pl.col("title").replace_strict(
            {t: c[1] for t, c in titles.items() if c}, default=None, return_dtype=pl.Int16
        ),
        county_code=pl.col("county").replace_strict(counties, default=None),
    )
    lost = frame.filter(pl.col("county_code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    frame = (
        frame.with_columns(
            candidate=pl.col("choice")
            .str.to_uppercase()
            .str.replace(r"\s*\([A-Z]+\)\s*[A-Z]?\s*$", "")
            .str.strip_chars(),
            place=pl.col("place").str.strip_chars(),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(
            candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"),
            kind=pl.lit("precinct"),
            code=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("GA"),
                "county_code",
                pl.col("county").str.to_uppercase(),
                "place",
                separator=":",
            ),
        )
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "district", "kind", "code", "county_code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("GA"),
            source=pl.lit("GA"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("year").agg(pl.col("code").n_unique()).sort("year").rows())


def emit() -> Path:
    year = int(os.environ["GA_YEAR"])
    frame = pl.read_parquet(PARSED / "precincts.parquet").filter(pl.col("year") == year)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Georgia's {year} general election by precinct (Secretary of State)",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for day, url in FILES.items():
            if day.year == year:
                cs.artifact(raw.cached(SOURCE, url), source)
        cs.scope_units("us.precinct", "____-__-__:GA:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '13%%'"
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
                   where x.scheme = 'us.contest' and x.value like %s""",
                [SOURCE, f"{year}-%%-%%:GA:%%"],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("GA"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            ).is_in(list(filled))
        )
        units = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
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
        medsl.attach(
            cs,
            conn,
            frame.drop("county_code"),
            {"GA": source},
            grain="precinct",
        )
        return cs.write()
