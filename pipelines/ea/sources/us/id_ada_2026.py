import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govcand, govmint
from ea.store import DATA, connect

SOURCE = "us.id_ada_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
GUIDE = "https://adacounty.id.gov/elections/wp-content/uploads/sites/38/Ada-County-Election-Guide-NOV-2026-1.pdf"
RESOLUTION = "https://files.smartsites.parentsquare.com/4192/new_business_supplemental_levy_election_resolution_august_2026.pdf"
PUBLISHER = "Ada County Elections"
TITLE = "Ada County Election Guide, November 3, 2026 General Election"
KUNA = "Kuna Joint School District No. 3"
ACHD = "Ada County Highway District"
CWI = "College of Western Idaho"


def text(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout.splitlines()


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = []
    body = ""
    seat = ""
    inside = False
    for line in text(GUIDE):
        line = line.strip()
        if line == "Ada County Highway District":
            inside = True
        elif line.startswith("what is on the ballot"):
            inside = False
        if not inside:
            continue
        if found := re.fullmatch(r"Ada County Highway District Commissioner District (\d+)", line):
            body, seat = ACHD, f"District {found[1]}"
        elif found := re.fullmatch(r"College of Western Idaho (\d+)", line):
            body, seat = CWI, f"Zone {found[1]}"
        elif body and re.fullmatch(r"[A-Z][A-Za-zñÑ.' -]+", line) and not re.match(r"(Ada County|College|Pursuant|Party)", line):
            rows.append(
                {
                    "county": "Ada",
                    "district": body,
                    "seat": seat,
                    "role": "Commissioner" if body == ACHD else "Trustee",
                    "seats": 1,
                    "name": line,
                    "unexpired": False,
                }
            )
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.rows())


WORDING = (
    "Shall the Board of Trustees of Joint School District No. 3, Ada and Canyon Counties, State of Idaho, be authorized "
    "and empowered to levy a Supplemental Levy in the amount of Three Million Six Hundred Nine Thousand Three Hundred "
    "Sixty Dollars ($3,609,360) per year for two (2) years, commencing with the fiscal year beginning July 1, 2027 and "
    "ending June 30, 2029, for the purposes stated above; all as provided in the Resolution adopted by the Board of "
    "Trustees on August 11, 2026? The estimated average annual cost to the taxpayer on the proposed levy is a tax of "
    "$53.64 per $100,000 of taxable assessed value, per year, based on current conditions. The proposed levy replaces "
    "an existing levy that expires on June 30, 2027, and that currently costs $53.64 per $100,000 of taxable assessed "
    "value. Therefore, if the proposed levy is approved, the tax per $100,000 of taxable assessed value is not "
    "expected to change."
)
ANSWERS = (
    "In favor of authorizing a supplemental levy in the amount of $3,609,360 per year for two (2) years",
    "Against authorizing a supplemental levy in the amount of $3,609,360 per year for two (2) years",
)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ada County, Idaho highway district and community college candidates and the Kuna school district supplemental levy on the November 3, 2026 ballot",
            polity="US-ID",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=GUIDE, license="Public record")
        cs.artifact(raw.cached(SOURCE, GUIDE), source)
        kept = govcand.races(conn, cs, SOURCE, "ID", DAY, frame, source, "16")
        home = govcand.counties(conn, "16")[ceda._core("Ada County")]
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/id/%%'").fetchall())
        minted = govmint.special(conn, cs, "ID", home, KUNA, slugs, function="school")
        if minted is None:
            raise SystemExit("no Kuna district")
        polity, area, place, path, code = minted
        parent = cs.key("us.election", f"{DAY}:ID")
        election = cs.key("us.election", f"{DAY}:ID:{code}")
        if election in cs.minted:
            cs.add("election", id=election, polity_id=polity, parent_id=parent, kind="general", date_from=DAY, date_to=DAY, date_precision="day", status="scheduled")
            cs.add("name", entity_id=election, text=f"{DAY.year} {place} general election", lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
        label = "Supplemental Levy"
        contest_key = f"{DAY}:ID:{code}:measure:{slug(label)}"
        contest = cs.key("us.contest", contest_key)
        cs.add("contest", id=contest, election_id=election, kind="ballot_question", district_area_id=area, electoral_system_id=cs.key("us.electoral_system", "referendum"), round_number=1, default_result_kind="counted")
        cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
        cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
        if contest in cs.minted:
            cs.add("slug", path=f"{path}/{DAY}-general/{slug(label)}", entity_id=contest)
        for position, answer in enumerate(ANSWERS):
            cs.add("contest_option", id=cs.key("us.option", f"{contest_key}:{slug(answer[:20])}"), contest_id=contest, kind="answer", ballot_position=position + 1, ballot_label=answer)
        resolution = cs.source("Kuna School District", "Supplemental Levy Election Resolution, August 2026")
        if resolution in cs.minted:
            cs.add("source", id=resolution, kind="official_body", publisher="Kuna School District", title="Supplemental Levy Election Resolution, August 2026", url=RESOLUTION, license="Public record")
        cs.artifact(raw.cached(SOURCE, RESOLUTION), resolution)
        cs.add("citation", entity_id=contest, source_id=resolution)
        cs.add("ballot_question_text", contest_id=contest, kind="text", lang="en", body=WORDING, url=RESOLUTION, source_id=resolution)
        print(f"{kept} candidates")
        return cs.write()
