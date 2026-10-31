from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ct_statement_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Connecticut Secretary of the State"
BASE = "https://portal.ct.gov/-/media/sots/electionservices/statementofvote_pdfs/{year}sovpdf.pdf"
URL = "https://portal.ct.gov/sots/election-services/statement-of-vote-pdfs/general-elections-statement-of-vote-1922"
TITLE = "Statement of Vote, General Election (Statement of Presiding Officers as to Votes Cast), 1948-1984"
TOTALS = {
    1948: (1033901, 890909),
    1952: (1185234, 1103598),
    1956: (1242267, 1126452),
    1960: (1320954, 1231885),
    1972: (1648017, 1409221),
    1976: (1677449, 1408480),
    1980: (1719108, 1423403),
    1984: (1809017, 1490946),
}


def fetch() -> None:
    for year in TOTALS:
        raw.fetch(SOURCE, BASE.format(year=year), suffix=".pdf")


def parse() -> None:
    frame = pl.DataFrame(
        [{"year": year, "registered": registered, "voters": voters} for year, (registered, voters) in TOTALS.items()]
    )
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
            summary="Connecticut's statewide whole number of names on the registry (check) list and whole number checked as having voted at the "
            "general elections of 1948, 1952, 1956, 1960, 1972, 1976, 1980, and 1984, from the Secretary of the State's statements of vote "
            "(scanned volumes read by OCR; each total equals the sum of its printed counties or congressional districts)",
            polity="US",
        )
        elections = {
            start.year: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':CT$' and e.kind = 'general' and extract(month from e.date_from) = 11"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        for year in TOTALS:
            cs.artifact(raw.cached(SOURCE, BASE.format(year=year)), source)
        for year, registered, voters in frame.select("year", "registered", "voters").iter_rows():
            if year not in elections:
                print(f"{year}: no election")
                continue
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
                    unit_id=cs.unit("us.state", "09"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
