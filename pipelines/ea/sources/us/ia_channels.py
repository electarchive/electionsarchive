import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import enr_clarity, medsl
from ea.store import DATA, connect

SOURCE = "us.ia_channels"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Iowa Secretary of State"
TITLE = "Election night reporting results by county"
FILES = [
    "https://results.enr.clarityelections.com/IA/115641/316303/reports/detailxml.zip",
    "https://results.enr.clarityelections.com/IA/122322/356043/reports/detailxml.zip",
]
CHANNELS = {"Election Day": 1, "Absentee": 3}
OFFICES = {
    "President and Vice President": "president",
    "United States Senator": "senate",
    "Governor and Lt. Governor": "governor",
    "Attorney General": "attorney-general",
    "Auditor of State": "auditor",
    "Secretary of Agriculture": "secretary-of-agriculture",
    "Secretary of State": "secretary-of-state",
    "Treasurer of State": "treasurer",
}
COURTS = {
    "Supreme Court Justice": "justice-of-the-supreme-court",
    "Court of Appeals Judge": "judge-of-the-court-of-appeals",
}


def fetch() -> None:
    for link in FILES:
        raw.fetch(enr_clarity.SOURCE, link, suffix=".zip")


def _chamber(text: str, day: str, contests: set[str]) -> tuple[str, int | None] | None:
    if text in OFFICES:
        return OFFICES[text], None
    if found := re.fullmatch(r"United States Representative District (\d+)", text):
        return "house", int(found[1])
    if found := re.fullmatch(r"State (Representative|Senator) District (\d+)", text):
        return f"{'house' if found[1] == 'Representative' else 'senate'}:{int(found[2])}:g", None
    if found := re.fullmatch(r"(Supreme Court Justice|Court of Appeals Judge) - (.+)", text):
        words = re.sub(r"[^a-z ]", "", found[2].lower()).split()
        return f"{COURTS[found[1]]}:{words[0]}-{words[-1]}", None
    if found := re.fullmatch(r"Constitutional Amendment (\d+)", text):
        tried = [f"question:constitutional-amendment-{found[1]}", f"question:medsl:constitutional-amendment-{found[1]}"]
        return next(((chamber, None) for chamber in tried if f"{day}:IA:{chamber}" in contests), None)
    return None


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%'"""
            ).fetchall()
        }
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value like '%%:IA:%%'"
            )
        }
    rows = []
    for link in FILES:
        with zipfile.ZipFile(raw.cached(enr_clarity.SOURCE, link).path) as archive:
            root = ElementTree.fromstring(archive.read("detail.xml"))
        day = datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date()
        for contest in root.iter("Contest"):
            found = _chamber(contest.get("text") or "", str(day), contests)
            if found is None:
                continue
            chamber, district = found
            for choice in contest.iter("Choice"):
                text = choice.get("text") or ""
                for kind in choice.iter("VoteType"):
                    for county in kind.iter("County"):
                        rows.append(
                            {
                                "year": day.year,
                                "chamber": chamber,
                                "day": day,
                                "district": district,
                                "code": counties[re.sub(r"[^a-z]", "", (county.get("name") or "").lower())],
                                "candidate": re.sub(r"\s+and\s+.*$", "", text).upper().strip(),
                                "channel": CHANNELS[kind.get("name") or ""],
                                "votes": int(county.get("votes") or 0),
                            }
                        )
    frame = (
        pl.DataFrame(rows, schema_overrides={"year": pl.Int32, "district": pl.Int16, "channel": pl.Int16})
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "district", "code", "candidate", "measure", "channel")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(special=pl.lit(False), state=pl.lit("IA"), kind=pl.lit("county"), source=pl.lit("IA"))
        .sort("day", "chamber", "district", "code", "candidate", "channel")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "channels.parquet")
    print(
        frame.group_by("year", "channel")
        .agg(pl.col("votes").sum(), pl.col("chamber").n_unique())
        .sort("year", "channel")
        .rows()
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "channels.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2022 and 2024 general elections by county and ballot type (election day and absentee), from the Secretary of State's results files",
            polity="US-IA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError("no Iowa Secretary of State source")
        for link in FILES:
            cs.artifact(raw.cached(enr_clarity.SOURCE, link), source)
        medsl.attach(cs, conn, frame, {"IA": source})
        return cs.write()
