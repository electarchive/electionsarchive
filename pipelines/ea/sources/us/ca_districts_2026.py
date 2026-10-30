import html
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
COUNTIES = {"06073": "San Diego", "06059": "Orange", "06067": "Sacramento"}
KEPT = re.compile(
    r"FIRE|WATER|UTILITY|HEALTH|IRRIGATION|WASTEWATER|SERVICES|SRVCS|PARK|LIBRARY|SANITATION|CEMETERY|SEWER|CONSERVATION|FLOOD"
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
    text = cased(label).replace(" Del ", " del ").replace(" And ", " and ")
    return text if re.search(r"\b(?:District|Authority|Agency|County)$", text) else f"{text} District"


def feed(fips: str) -> tuple[str, str, str]:
    if fips in local_2026.FILES:
        return (local_2026.FILES[fips], *local_2026.PUBLISHERS[fips][1:])
    return local_2026.FEEDS[f"CA-{fips}"]


def lines(fips: str) -> list[str]:
    path = raw.cached(local_2026.SOURCE, feed(fips)[0]).path
    return (
        subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
        .stdout.decode()
        .splitlines()
    )


def san_diego() -> list[dict[str, str | int | bool]]:
    rows = []
    text = lines("06073")
    keep = False
    for index, line in enumerate(text):
        if found := re.match(r" Election = (YES|NO)\s+Vote for (\d+)", line):
            heading = next(previous.strip() for previous in reversed(text[:index]) if previous.strip())
            seats = int(found[2])
            unexpired = "Short Term" in text[index + 1] or "(Short Term)" in heading
            keep = found[1] == "YES" and KEPT.search(heading) is not None and DROPPED.search(heading) is None
            seat = SEAT.match(re.sub(r"\s*\(Short Term\)", "", heading))
            body, label = (
                (seat[1], f"{'Zone' if 'ZONE' in heading.upper() else 'Division'} {seat[2]}") if seat else (heading, "")
            )
            body = re.sub(r"\s*\(.*$", "", body)
        elif keep and (person := PERSON.match(line)) and person[4] == "YES":
            rows.append(
                {
                    "county": "San Diego",
                    "district": full_name(body),
                    "seat": label or ("Short Term" if unexpired else ""),
                    "role": "Director",
                    "seats": seats,
                    "name": cased(f"{person[2]} {person[1]}"),
                    "unexpired": unexpired,
                }
            )
    return rows


def orange() -> list[dict[str, str | int | bool]]:
    rows = []
    heading = seat = role = ""
    seats = 1
    keep = unexpired = False
    for line in lines("06059"):
        if found := re.match(r"^Nonpartisan (.+) (Director|Trustee)$", line):
            heading, role = found[1], found[2]
            keep = re.search(r"SCHOOL|COLLEGE|^COUNTY|CITY OF|COUNCIL", heading) is None
            seat, unexpired = "", False
        elif keep and (found := re.match(r"^\s+\*?\s*\d{4}\s+(.+?)\s*$", line)):
            number = re.search(r"Div(?:ision|\.)? (\d+)", found[1])
            seat = f"Division {number[1]}" if number else ""
            unexpired = "Short Term" in found[1]
            if heading.startswith("MUNICIPAL WATER"):
                heading = "Municipal Water District of Orange County"
        elif keep and (found := re.search(r"\s(\d+)\s+\d+\s+\d+\s+ON BALLOT\s*$", line)):
            seats = int(found[1])
        elif keep and (found := re.fullmatch(r"(?:\s{8}Candidate\(s\):\s+| {33})([A-Z][A-Z .'\"-]{3,})", line)):
            rows.append(
                {
                    "county": "Orange",
                    "district": full_name(re.sub(r"\bDIST\.", "DISTRICT", heading)),
                    "seat": seat or ("Short Term" if unexpired else ""),
                    "role": role,
                    "seats": seats,
                    "name": cased(found[1]),
                    "unexpired": unexpired,
                }
            )
    return rows


SACRAMENTO = re.compile(
    r"^(?P<body>.+?),? (?P<role>Director|Trustee)(?:,? (?P<seat>(?:Division|District|Ward) \d+))?(?P<short>\s*-\s*Short Term)?\s*$"
)


def sacramento() -> list[dict[str, str | int | bool]]:
    rows = []
    page = raw.cached(local_2026.SOURCE, feed("06067")[0]).path.read_text()
    text = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?s)<(script|style).*?</\1>", "", page)))
    text = text.replace("\xa0", " ")
    text = [line.strip() for line in text.splitlines() if line.strip()]
    contest = None
    seats = 1
    live = False
    for index, line in enumerate(text):
        if found := re.match(r"Contest: \d+\s*-\s*(.+)$", line):
            contest = SACRAMENTO.match(" ".join(found[1].split()))
            if contest is not None and not KEPT.search(contest["body"].upper()):
                contest = None
            seats, live = 1, False
        elif contest is None:
            continue
        elif found := re.match(r"Vote for (\d+)", line):
            seats = int(found[1])
        elif line.startswith("On Ballot:"):
            live = line.endswith("Yes")
        elif live and line == "Qualified" and re.fullmatch(r"\d+", text[index - 1]):
            body = contest["body"]
            if DROPPED.search(body.upper()) or re.search(r"(?i)^city of|school|college|^el dorado", body):
                continue
            seat = contest["seat"] or ""
            rows.append(
                {
                    "county": "Sacramento",
                    "district": full_name(body),
                    "seat": seat or ("Short Term" if contest["short"] else ""),
                    "role": contest["role"],
                    "seats": seats,
                    "name": cased(re.sub(r"\s{2,}.*$", "", text[index + 1])),
                    "unexpired": contest["short"] is not None,
                }
            )
    return rows


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame([*san_diego(), *orange(), *sacramento()]).unique(
        ["county", "district", "seat", "name"], keep="first", maintain_order=True
    )
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
            url, *publisher = feed(fips)
            source = cs.source(*publisher)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher[0],
                    title=publisher[1],
                    url=url,
                    license="Public record",
                )
            cs.artifact(raw.cached(local_2026.SOURCE, url), source)
            kept += govcand.races(conn, cs, SOURCE, "CA", DAY, frame.filter(pl.col("county") == county), source, "06")
        print(f"{kept} candidates")
        return cs.write()
