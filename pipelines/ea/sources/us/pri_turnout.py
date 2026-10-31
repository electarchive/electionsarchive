import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.pri_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KY = "https://elect.ky.gov"
AL = "https://www.sos.alabama.gov/sites/default/files/election-data/"
FILES = {
    ("KY", date(2014, 5, 20)): KY
    + "/SiteCollectionDocuments/Election%20Statistics/turnout/2011-2019/voterturnoutcounty-2014P-20140922-022341.pdf",
    ("KY", date(2016, 5, 17)): KY + "/statistics/Documents/voterturnoutcounty-2016P-20160815-115948.pdf",
    ("KY", date(2018, 5, 22)): KY + "/Resources/Documents/voterturnoutcounty-2018P-20180824-072344.pdf",
    ("KY", date(2020, 6, 23)): KY + "/Resources/Documents/voterturnoutcounty-2020P-20200823-070314.pdf",
    ("KY", date(2022, 5, 17)): KY + "/Resources/Documents/Primary2022County.pdf",
    ("KY", date(2024, 5, 21)): KY + "/Resources/Documents/2024P%20Voter%20Turnout%20by%20County.pdf",
    ("AL", date(2022, 5, 24)): AL + "2022-08/2022%20Primary%20Election%20Total%20Ballots%20Cast.pdf",
    ("AL", date(2024, 3, 5)): AL + "2025-12/2024_Primary_Election_Total_Ballots_Cast.pdf",
}
COUNTS = {"KY": 120, "AL": 67}
FIPS = {"KY": "21", "AL": "01"}
ROWS = {
    "KY": re.compile(r"^\s*\d{3}\s+([A-Z][A-Z' .]+?)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+\s*%"),
    "AL": re.compile(r"^\s*([A-Z][A-Za-z' .]+?)\s+([\d,]+)\s+([\d,]+)\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+[\d.]+%"),
}
PUBLISHERS = {"KY": "Kentucky State Board of Elections", "AL": "Alabama Secretary of State"}
TITLES = {"KY": "Voter turnout report by county", "AL": "Official total ballots cast"}
PAGES = {
    "KY": "https://elect.ky.gov/Resources/Pages/Turnout.aspx",
    "AL": "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows: list[tuple[str, date, str, int, int]] = []
    for (state, day), url in FILES.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        seen: dict[str, tuple[int, int]] = {}
        for line in lines:
            found = ROWS[state].match(line)
            if found and found[1].strip() not in seen and not found[1].upper().startswith("TOTAL"):
                seen[found[1].strip()] = (int(found[2].replace(",", "")), int(found[3].replace(",", "")))
        if len(seen) != COUNTS[state]:
            print(f"{state} {day}: {len(seen)} counties")
        rows.extend((state, day, county, registered, ballots) for county, (registered, ballots) in seen.items())
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["state", "day", "county", "registered", "ballots"], orient="row").write_parquet(
        PARSED / "turnout.parquet"
    )
    print(len(rows), "county rows")


def emit() -> Path:
    voters = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Registered voters and ballots cast by county for Kentucky's primaries of 2014 to 2024 and Alabama's of 2022 and 2024",
            polity="US",
        )
        sources = {}
        for state in FIPS:
            sources[state] = cs.source(PUBLISHERS[state], TITLES[state])
            cs.add(
                "source",
                id=sources[state],
                kind="official_body",
                publisher=PUBLISHERS[state],
                title=TITLES[state],
                url=PAGES[state],
                license="Public record",
            )
        for (state, _), url in FILES.items():
            cs.artifact(raw.cached(SOURCE, url), sources[state])
        for state, prefix in FIPS.items():
            counties = {
                re.sub(r"[^a-z]", "", text.lower()): unit
                for unit, text in conn.execute(
                    """select u.id, n.text from reporting_unit u
                       join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like %s""",
                    [prefix + "%%"],
                ).fetchall()
            }
            for day in voters.filter(pl.col("state") == state)["day"].unique().sort():
                elections = {
                    election_id
                    for (election_id,) in conn.execute(
                        """select c.election_id from external_id x join contest c on c.id = x.entity_id
                           join election e on e.id = c.election_id and e.kind = 'primary'
                           where x.scheme = 'us.contest' and x.value like %s""",
                        [f"{day}:{state}:%"],
                    )
                }
                if len(elections) != 1:
                    print(f"{state} {day}: {len(elections)} elections, skipped")
                    continue
                (election,) = elections
                result_set = cs.key(f"{SOURCE}.turnout", f"{state}:{day}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[state],
                    is_default=election not in held,
                    completeness=1,
                )
                for county, registered, ballots in (
                    voters.filter((pl.col("state") == state) & (pl.col("day") == day))
                    .select("county", "registered", "ballots")
                    .iter_rows()
                ):
                    for measure, value in ((2, registered), (3, ballots)):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=UUID(
                                str(counties[re.sub(r"[^a-z]", "", county.lower().replace("tucsaloosa", "tuscaloosa"))])
                            ),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
