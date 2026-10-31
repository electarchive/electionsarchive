import html
import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.dc_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "District of Columbia Board of Elections"
SUMMARIES = {
    2002: (
        "https://dcboe.org/elections/election-results-archives/november-5-general-election/certification-summary-candidate",
        ".html",
    ),
    2004: ("https://dcboe.org/getmedia/ffa45adf-8210-4d5b-8160-4d1609feeaf0/Summary_2.pdf", ".pdf"),
    2006: ("https://dcboe.org/getmedia/77fd9c1b-bb65-473b-8c1c-4530af2e2f2f/Summary_General_2006.pdf", ".pdf"),
    2008: (
        "https://dcboe.org/getmedia/a484dffd-ad98-4063-a951-5787e9cb5f85/General_08_Certified_Results_Summary_1.pdf",
        ".pdf",
    ),
}


def fetch() -> None:
    for url, suffix in SUMMARIES.values():
        raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    rows = []
    for year, (url, suffix) in SUMMARIES.items():
        path = raw.cached(SOURCE, url).path
        if suffix == ".html":
            text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", path.read_text(errors="replace"))))
            registered = int(re.search(r"REGISTERED VOTERS - TOTAL[ .]*([\d,]+)", text)[1].replace(",", ""))
            voters = int(re.search(r"BALLOTS CAST - TOTAL[ .]*([\d,]+)", text)[1].replace(",", ""))
        else:
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            ).stdout
            if year == 2004:
                registered = int(re.search(r"Total Registration\s+([\d,]+)", text)[1].replace(",", ""))
                voters = int(re.search(r"Total Ballots Cast\s+([\d,]+)", text)[1].replace(",", ""))
            else:
                registered = int(re.search(r"Registration & Turnout\s+([\d,]+)\s+Voters", text)[1].replace(",", ""))
                voters = sum(
                    int(re.search(rf"{kind} Turnout\s+([\d,]+)", text)[1].replace(",", ""))
                    for kind in ("Election Day", "Absentee", "Provisional")
                )
        rows.append({"year": year, "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The District of Columbia's registered voters and ballots cast at its 2002, 2004, 2006, and 2008 "
            "general elections, from the Board of Elections' certified summary reports",
            polity="US",
        )
        elections = {
            start.year: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':DC(:[a-z]+)?$' and e.kind = 'general'
                   and e.date_from between '2002-01-01' and '2008-12-31'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for year, registered, voters in frame.select("year", "registered", "voters").iter_rows():
            url, _ = SUMMARIES[year]
            title = f"Certified summary report, {year} general election"
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url), source)
            value_key, election_id = elections[year]
            result_set = cs.key(f"{SOURCE}.result_set", value_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=1,
            )
            for measure, value in ((2, registered), (14, voters)):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "11"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
