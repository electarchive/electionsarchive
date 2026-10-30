import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import govcand, local_2026
from ea.sources.us.sld_primaries_2026 import cased
from ea.store import DATA, connect

SOURCE = "us.ca_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
COUNTIES = {"06073": "San Diego"}
KEPT = re.compile(
    r"FIRE|WATER|UTILITY|HEALTH|IRRIGATION|WASTEWATER|SERVICES|SRVCS|PARK|LIBRARY|SANITATION|CEMETERY|SEWER"
)
DROPPED = re.compile(r"SCHL|SCHOOL|COLLEGE|COMM PLAN|PLANNING|SUPERVISORS|ASSESSOR|JUDGE|COURT|^CITY")
WORDS = (
    (r"\bMUNI\b", "MUNICIPAL"),
    (r"\bCONS\b", "CONSOLIDATED"),
    (r"\bPROT\b", "PROTECTION"),
    (r"\bSRVCS\b", "SERVICES"),
    (r"\bHEATLH\b", "HEALTH"),
)
SEAT = re.compile(
    r"^(.+?)\s+-\s+(?:DIV(?:ISION)?\.?|ZONE|AREA)\s+(?:NO\.\s+)?(\w+)(?:\s+\(Short Term\))?$", re.IGNORECASE
)
PERSON = re.compile(r"^([A-Z][^,]*?), (.+?)(?: - ([A-Z]{2,4}))?\s{3,}\S.*\s(YES|NO)\s*$")


def full_name(label: str) -> str:
    for short, full in WORDS:
        label = re.sub(short, full, label)
    text = cased(label).replace(" Del ", " del ")
    return text if re.search(r"\b(?:District|Authority|Agency)$", text) else f"{text} District"


def parse() -> None:
    rows = []
    for fips, county in COUNTIES.items():
        url = local_2026.FILES[fips]
        path = raw.cached(local_2026.SOURCE, url).path
        lines = (
            subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
            .stdout.decode()
            .splitlines()
        )
        keep = False
        for index, line in enumerate(lines):
            if found := re.match(r" Election = (YES|NO)\s+Vote for (\d+)", line):
                heading = next(previous.strip() for previous in reversed(lines[:index]) if previous.strip())
                seats = int(found[2])
                unexpired = "Short Term" in lines[index + 1] or "(Short Term)" in heading
                keep = found[1] == "YES" and KEPT.search(heading) is not None and DROPPED.search(heading) is None
                seat = SEAT.match(re.sub(r"\s*\(Short Term\)", "", heading))
                body, label = (
                    (seat[1], f"{'Zone' if 'ZONE' in heading.upper() else 'Division'} {seat[2]}")
                    if seat
                    else (heading, "")
                )
                body = re.sub(r"\s*\(.*$", "", body)
            elif keep and (person := PERSON.match(line)) and person[4] == "YES":
                rows.append(
                    {
                        "county": county,
                        "district": full_name(body),
                        "seat": label,
                        "role": "Director",
                        "seats": seats,
                        "name": cased(f"{person[2]} {person[1]}"),
                        "unexpired": unexpired,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["county", "district", "seat", "name"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame.select("county", "district").unique().height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California special district board races on the November 3, 2026 ballot, with candidates from county registrar candidate lists",
            polity="US-CA",
        )
        kept = 0
        for fips, county in COUNTIES.items():
            publisher = local_2026.PUBLISHERS[fips][1:]
            source = cs.source(*publisher)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher[0],
                    title=publisher[1],
                    url=local_2026.FILES[fips],
                    license="Public record",
                )
            cs.artifact(raw.cached(local_2026.SOURCE, local_2026.FILES[fips]), source)
            kept += govcand.races(conn, cs, SOURCE, "CA", DAY, frame.filter(pl.col("county") == county), source, "06")
        print(f"{kept} candidates")
        return cs.write()
