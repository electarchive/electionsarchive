import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mo_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
SITE = "https://web.archive.org/web/2025id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
FILES = {
    date(2000, 11, 7): "Voter_Turnout_11-07-00_GENERAL.pdf",
    date(2002, 11, 5): "Voter_Turnout_11-05-02_GENERAL.pdf",
    date(2004, 11, 2): "Voter_Turnout_11-02-04_GENERAL.pdf",
    date(2006, 11, 7): "Voter_Turnout_11-07-06_GENERAL.pdf",
    date(2008, 11, 4): "Voter_Turnout_11-05-08_GENERAL.pdf",
    date(2010, 11, 2): "Voter_Turnout_11-02-10_GENERAL.pdf",
    date(2012, 11, 6): "Voter_Turnout_11-06-12_GENERAL.pdf",
    date(2014, 11, 4): "ActualVoterTurnout-General2014forweb.pdf",
    date(2016, 11, 8): "VoterTurnoutNov2016.pdf",
    date(2018, 11, 6): "2018GeneralElectionTurnout.pdf",
    date(2020, 11, 3): "Nov2020OfficialVoterTurnout.pdf",
    date(2022, 11, 8): "Nov2022OfficialVoterTurnout.pdf",
    date(2024, 11, 5): "Nov2024OfficialVoterTurnout.pdf",
}
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '29%' and u.code <> '29510'
"""
ROW = re.compile(r"^\s*([A-Za-z.' ]+?)\s{2,}(\d[\d,]*(?:\s+[\d,%.]+)*)\s*$")
SKIPPED = {"kansascity", "jackson", "total", "totals"}


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for file in FILES.values():
        raw.fetch(SOURCE, SITE + file, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    codes["stlouiscounty"] = codes["stlouis"]
    rows = []
    for day, file in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + file).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        text = re.sub(r"(?i)(ST\.? LOUIS)\s*\n\s*(COUNTY)", r"\1 \2", text)
        text = re.sub(r"(?i)(CAPE)\s*\n\s*(GIRARDEAU)", r"\1 \2", text)
        found = {}
        for line in text.splitlines():
            match = ROW.match(line)
            if match is None or plain(match[1]) in SKIPPED:
                continue
            key = plain(match[1])
            if key == "stlouiscity":
                continue
            if key not in codes:
                continue
            numbers = [int(t.replace(",", "")) for t in match[2].split() if re.fullmatch(r"[\d,]+", t)]
            if len(numbers) not in (2, 4) or numbers[-1] > numbers[0]:
                print(f"{file} {match[1]}: {numbers}")
                continue
            found[codes[key]] = (numbers[0], numbers[-1])
        print(file, day, len(found))
        for code, (registered, voters) in found.items():
            rows.append({"date": day, "code": code, "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's registered voters and votes cast in the highest-turnout race by county for the generals of 2000–2024 "
            "from the Secretary of State's voter turnout reports",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':MO$' and e.kind = 'general'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Turnout Report"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://www.sos.mo.gov/elections/results",
                license="Public record",
            )
        for (day,), rows in frame.group_by("date", maintain_order=True):
            found = elections.get(day)
            if found is None:
                print(f"{day}: no election")
                continue
            value_key, election_id = found
            cs.artifact(raw.cached(SOURCE, SITE + FILES[day]), source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
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
                completeness=round(rows.height / 114, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
