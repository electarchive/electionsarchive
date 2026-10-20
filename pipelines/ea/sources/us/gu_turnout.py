import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.gu_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Guam Election Commission"
BASE = "https://gwosa2.opaguam.org/sites/default/files/"
REPORTS = {
    "gec_ccr14.pdf": "Citizen-Centric Report, fiscal year 2014",
    "gec_ccr15.pdf": "Citizen-Centric Report, fiscal year 2015",
    "gec_ccr22_0.pdf": "Citizen-Centric Report, fiscal year 2022",
    "gec_ccr24.pdf": "Citizen-Centric Report, fiscal year 2024",
}
NUMBERS = r"((?:[\d,]+\s+){1,5}[\d,]+)"


def fetch() -> None:
    for name in REPORTS:
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def text(name: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def parse() -> None:
    rows = []
    for name, stage, first in (("gec_ccr14.pdf", "primary", 2014), ("gec_ccr15.pdf", "general", 2014)):
        body = text(name)
        registered = re.search(r"Registered Voters\s+((?:[\d,]+\s+){5}[\d,]+)", body)
        voted = re.search(r"Total Voted\s+((?:[\d,]+\s+){5}[\d,]+)", body)
        if registered is None or voted is None:
            raise ValueError(name)
        for year, r, v in zip(range(first, first - 12, -2), registered[1].split(), voted[1].split()):
            rows.append(
                {"year": year, "stage": stage, "registered": int(r.replace(",", "")), "voters": int(v.replace(",", "")), "file": name}
            )
    for name in ("gec_ccr22_0.pdf", "gec_ccr24.pdf"):
        lines = text(name).splitlines()
        for i, line in enumerate(lines):
            labels = re.findall(r"(20\d\d) (General|Primary)", line)
            if len(labels) < 3 or "Total No. of Registered Voters" in line:
                continue
            window = "\n".join(lines[i : i + 6])
            registered = re.search(r"Total No. of Registered Voters\s+([\d,\s]+)", window)
            ballots = re.search(r"Total No. of Cast Ballots\s+([\d,\s]+)", window)
            if registered is None or ballots is None:
                continue
            reg = re.findall(r"\d[\d,]+", registered[1])
            cast = re.findall(r"\d[\d,]+", ballots[1])
            if len(reg) < len(labels) or len(cast) < len(labels):
                continue
            for (year, kind), r, v in zip(labels, reg, cast):
                rows.append(
                    {
                        "year": int(year),
                        "stage": kind.lower(),
                        "registered": int(r.replace(",", "")),
                        "voters": int(v.replace(",", "")),
                        "file": name,
                    }
                )
    frame = pl.DataFrame(rows).unique(["year", "stage", "registered", "voters"], keep="first").sort("stage", "year")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.group_by("stage").len())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    dup = frame.group_by("year", "stage").len().filter(pl.col("len") > 1)
    if dup.height:
        raise ValueError(dup)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Guam's registered voters and ballots cast for its 2004–2024 primaries and generals, "
            "from the Election Commission's citizen-centric reports",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':GU(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        sources = {}
        for name, title in REPORTS.items():
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=BASE + name, license="Public record")
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
            sources[name] = source
        for year, stage, registered, voters, name in frame.select("year", "stage", "registered", "voters", "file").iter_rows():
            found = elections.get((year, stage), [])
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            result_set = cs.key(f"{SOURCE}.result_set", value_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[name],
                is_default=election_id not in held,
                completeness=1,
            )
            for measure, value in ((2, registered), (14, voters)):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "66"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
