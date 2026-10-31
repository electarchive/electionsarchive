import re
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.state_primaries import SC_FILES, SC_SITE
from ea.store import DATA, connect

SOURCE = "us.sc_federal"
CACHE = "us.state_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = [day for day in SC_FILES if day.year >= 2010]
CONTEST = re.compile(r"U\.S\. (House of Representatives, District (\d+)|Senate) - (DEM|REP)")
PUBLISHER = "South Carolina State Election Commission"
TITLE = "Election night reporting: detail results"


def archive(election: str) -> raw.Artifact:
    site = SC_SITE.format(election)
    version = raw.cached(CACHE, site + "current_ver.txt").path.read_text().strip()
    return raw.cached(CACHE, f"{site}{version}/reports/detailxml.zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    rows = []
    for day in DAYS:
        tallied: dict[tuple[str, int | None, str], list[tuple[str, str, int]]] = {}
        for election in SC_FILES[day]:
            with zipfile.ZipFile(archive(election).path) as zipped:
                root = ElementTree.fromstring(zipped.read("detail.xml"))
            for race in root.iter("Contest"):
                match = CONTEST.fullmatch(re.sub(r"\s+", " ", race.get("text", "")).strip())
                if match is None:
                    continue
                key = (
                    "house" if match[2] else "senate",
                    int(match[2]) if match[2] else None,
                    "democratic" if match[3] == "DEM" else "republican",
                )
                found = []
                for choice in race.iter("Choice"):
                    counts: dict[str, int] = {}
                    for kind in choice.iter("VoteType"):
                        for county in kind:
                            if county.tag in ("County", "Precinct"):
                                counts[county.get("name", "")] = counts.get(county.get("name", ""), 0) + int(
                                    county.get("votes", "0")
                                )
                    if sum(counts.values()) != int(choice.get("totalVotes", "0")):
                        raise ValueError(f"{election} {key}: the county rows do not match the candidate's total")
                    candidate = re.sub(r"\s*\(Withdrew\)$", "", re.sub(r"\s+", " ", choice.get("text", ""))).strip()
                    found.extend((county, candidate, value) for county, value in counts.items())
                tallied[key] = found
        rows.extend(
            (
                day.year,
                f"{office}:{party}",
                day,
                False,
                "SC",
                district,
                counties[re.sub(r"[^a-z]", "", county.lower())],
                candidate.upper(),
                1,
                False,
                value,
            )
            for (office, district, party), found in tallied.items()
            for county, candidate, value in found
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("SC")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="South Carolina's 2010–2022 U.S. House and Senate primaries and runoffs by county",
            polity="US-SC",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.enr-scvotes.org/",
            license="Public record",
        )
        for day in DAYS:
            for election in SC_FILES[day]:
                cs.artifact(archive(election), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"SC": source}, strict=True)
        return cs.write()
