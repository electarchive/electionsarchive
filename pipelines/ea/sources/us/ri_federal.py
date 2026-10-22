import html
import json
import re
from datetime import date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ri_federal"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://rigov.s3.amazonaws.com/election/results/"
FOLDERS = [
    "2012/presidential_preference_primary",
    "2012/statewide_primary",
    "2014/statewide_primary",
    "2016/presidential_preference_primary",
    "2016/statewide_primary",
    "2018/statewide_primary",
    "2020/presidential_preference_primary",
    "2020/statewide_primary",
    "2022/statewide_primary",
]
COUNTIES = {
    "44001": "barrington bristol warren",
    "44003": "coventry east_greenwich warwick west_greenwich west_warwick",
    "44005": "jamestown little_compton middletown newport portsmouth tiverton",
    "44007": "burrillville central_falls cranston cumberland east_providence foster glocester johnston lincoln north_providence north_smithfield pawtucket providence scituate smithfield woonsocket",
    "44009": "charlestown exeter hopkinton new_shoreham narragansett north_kingstown richmond south_kingstown westerly",
}
TOWNS = {town: code for code, towns in COUNTIES.items() for town in towns.split()}
MISSING = {("2020/statewide_primary", town) for town in ("barrington", "warren", "lincoln")}
PARTIES = {"DEM": "democratic", "REP": "republican"}
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Election results by city and town"
ARCHIVE = "https://web.archive.org/web/2009id_/http://www.elections.ri.gov/elections/results/2008/presidential_preference_primary/"


def fetch() -> None:
    for folder in FOLDERS:
        for town in [*TOWNS, "statewide"]:
            try:
                raw.fetch(SOURCE, f"{BASE}{folder}/{town}.json", suffix=".json")
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 403:
                    raise
                print("no file:", folder, town)
    for town in [*TOWNS, "statewide"]:
        raw.fetch(
            SOURCE, ARCHIVE + ("statewidesummary.php" if town == "statewide" else f"{town}/index.php"), suffix=".html"
        )


def parse() -> None:
    rows = []
    for folder in [*FOLDERS, "2008/presidential_preference_primary"]:
        totals: dict[tuple[str, str, str], int] = {}
        statewide: dict[tuple[str, str, str], int] = {}
        for town in [*TOWNS, "statewide"]:
            if (folder, town) in MISSING:
                continue
            if folder in FOLDERS:
                data = json.loads(raw.cached(SOURCE, f"{BASE}{folder}/{town}.json").path.read_text())
                day = datetime.strptime(data["election_date"], "%B %d, %Y").date()
            else:
                page = raw.cached(
                    SOURCE, ARCHIVE + ("statewidesummary.php" if town == "statewide" else f"{town}/index.php")
                )
                lines = [
                    line.strip()
                    for line in html.unescape(
                        re.sub(r"<[^>]+>", "\n", page.path.read_text(errors="replace"))
                    ).splitlines()
                    if line.strip()
                ]
                day, data, current = date(2008, 3, 4), {"contests": []}, None
                for n, line in enumerate(lines):
                    if line.endswith("PRESIDENTIAL CANDIDATE"):
                        current = {"name": line, "candidates": []}
                        data["contests"].append(current)
                    elif line.startswith("DELEGATES") or line == "Contact Us":
                        current = None
                    elif (
                        current is not None
                        and line not in ("Candidate", "Total votes")
                        and n + 1 < len(lines)
                        and re.fullmatch(r"[\d,]+", lines[n + 1])
                    ):
                        current["candidates"].append({"name": line, "votes": lines[n + 1]})
            for contest in data["contests"]:
                name = contest["name"].upper()
                if "GENERAL ASSEMBLY" in name or "DELEGATE" in name:
                    continue
                if name.endswith("PRESIDENTIAL CANDIDATE"):
                    chamber, district = "president", None
                    designation = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}.get(name.split()[0])
                elif "SENATOR IN CONGRESS" in name or name.endswith("U.S. SENATOR"):
                    chamber, district, designation = "senate", None, None
                elif "REPRESENTATIVE IN CONGRESS DISTRICT" in name:
                    chamber, district, designation = "house", int(name.rsplit(" ", 1)[1]), None
                else:
                    continue
                for candidate in contest["candidates"]:
                    party = PARTIES.get(
                        designation or candidate.get("party_code") or contest.get("contest_party_designation")
                    )
                    if party is None:
                        continue
                    name = re.sub(r"\s+", " ", html.unescape(candidate["name"])).rstrip("*").strip().upper()
                    key = (f"{chamber}:{party}", str(district), "WRITE-INS" if name == "WRITE-IN" else name)
                    votes = int(candidate["votes"].replace(",", ""))
                    if town == "statewide":
                        statewide[key] = statewide.get(key, 0) + votes
                        continue
                    totals[key] = totals.get(key, 0) + votes
                    rows.append(
                        (day.year, key[0], day, False, "RI", district, TOWNS[town], key[2], 1, key[2] == "WRITE-INS")
                        + (votes, "county", 0, 0)
                    )
        for key, votes in statewide.items():
            if key[0].startswith("president:"):
                mail = votes - totals.pop(key, 0)
                statewide[key] = 0
                if mail < 0:
                    raise ValueError(f"{folder} {key}: towns exceed the statewide total")
                rows.append(
                    (day.year, key[0], day, False, "RI", None, "RI:statewide", key[2], 1, key[2] == "WRITE-INS")
                    + (mail, "aggregate", 3, 33)
                )
        statewide = {key: votes for key, votes in statewide.items() if not key[0].startswith("president:")}
        if totals != statewide:
            print(
                folder,
                "towns differ from the statewide file:",
                {
                    k: (totals.get(k), statewide.get(k))
                    for k in set(totals) | set(statewide)
                    if totals.get(k) != statewide.get(k)
                },
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
            "kind": pl.String,
            "channel": pl.Int16,
            "flags": pl.Int16,
        },
        orient="row",
    ).group_by(
        "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein", "kind"
    ).agg(pl.col("votes").sum(), pl.col("channel").first(), pl.col("flags").first()).with_columns(
        source=pl.lit("RI")
    ).write_parquet(PARSED / "counties.parquet")
    print(len(rows), "rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2008 to 2022 president, U.S. Senate, and House primaries by county, with the presidential primaries' centrally counted mail ballots as a statewide row",
            polity="US-RI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ri.gov/election/results/",
            license="Public record",
        )
        for folder in FOLDERS:
            for town in [*TOWNS, "statewide"]:
                if (folder, town) in MISSING:
                    continue
                cs.artifact(raw.cached(SOURCE, f"{BASE}{folder}/{town}.json"), source)
        for town in [*TOWNS, "statewide"]:
            cs.artifact(
                raw.cached(SOURCE, ARCHIVE + ("statewidesummary.php" if town == "statewide" else f"{town}/index.php")),
                source,
            )
        frame = pl.read_parquet(PARSED / "counties.parquet")
        medsl.attach(cs, conn, frame, {"RI": source}, strict=True)
        staged = {str(row["id"]) for row in cs.rows.get("result_set", [])}
        for (day, chamber), lines in frame.filter(pl.col("chamber").str.starts_with("president:")).group_by(
            "day", "chamber"
        ):
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:RI:{chamber}:county")
            if str(result_set) not in staged:
                continue
            towns = lines.filter(pl.col("kind") == "county")["votes"].sum()
            total = lines["votes"].sum()
            cs.add(
                "note",
                id=(
                    conn.execute(
                        "select id from note where entity_id = %s and kind = 'caveat'", [result_set]
                    ).fetchone()
                    or (None,)
                )[0],
                entity_id=result_set,
                kind="caveat",
                body_md=f"The Board of Elections' statewide file counts {total:,} votes and its 39 town files "
                f"{towns:,}. The {total - towns:,}-vote difference, the mail ballots the Board counts centrally, "
                "is one statewide row per candidate: the statewide total less the town sum.",
                valid_from_precision="day",
                valid_to_precision="day",
            )
        return cs.write()
