import io
import os
import re
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.al_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alabama Secretary of State"
TITLE = "Election Data Downloads: precinct-level general election results"
LANDING = "https://www.sos.alabama.gov/alabama-votes/voter/election-data"
BASE = "https://www.sos.alabama.gov/sites/default/files/election-data/2017-06/"
FILES = {
    date(2004, 11, 2): "https://www.sos.alabama.gov/sites/default/files/election-data/2023-06/2004-GeneralElection-PrecinctLevel.zip",
    date(2002, 11, 5): BASE + "2002-GeneralElection-PrecinctLevel_0.xls",
    date(2010, 11, 2): BASE + "2010-general-precinctLevel.zip",
    date(2012, 11, 6): BASE + "2012General-PrecinctLevel.zip",
    date(2014, 11, 4): BASE + "2014General-precinctLevel.zip",
}
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
COUNTYWIDE = r"ABSENTEE|PROVISIONAL|MAIL|CURBSIDE"
TOTALS = r"TOTAL|REGISTERED|BALLOTS CAST|CALCULATED|REPORTED"
STATEWIDE = {
    "GOVERNOR": "governor",
    "LIEUTENANT GOVERNOR": "lieutenant-governor",
    "ATTORNEY GENERAL": "attorney-general",
    "SECRETARY OF STATE": "secretary-of-state",
    "STATE TREASURER": "treasurer",
    "STATE AUDITOR": "auditor",
    "COMMISSIONER OF AGRICULTURE AND INDUSTRIES": "commissioner-of-agriculture-and-industries",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=url[-4:])


def xml_sheets(content: bytes) -> list[list[list[str]]]:
    sheets = []
    for sheet in ET.fromstring(content.lstrip(b"\xef\xbb\xbf")).iter(SS + "Worksheet"):
        rows = []
        for row in sheet.iter(SS + "Row"):
            cells: list[str] = []
            for cell in row.findall(SS + "Cell"):
                index = cell.get(SS + "Index")
                if index is not None:
                    cells.extend([""] * (int(index) - 1 - len(cells)))
                data = cell.find(SS + "Data")
                cells.append((data.text or "").strip() if data is not None else "")
                cells.extend([""] * int(cell.get(SS + "MergeAcross") or 0))
            rows.append(cells)
        sheets.append(rows)
    return sheets


def contest(title: str) -> tuple[str, int | None] | None:
    name = re.sub(r"\s+", " ", re.sub(r"\(VOTE FOR \d+\)", "", re.sub(r"^FOR ", "", title.strip().upper()))).strip(" ,")
    name = {"LT. GOVERNOR": "LIEUTENANT GOVERNOR", "ATTORNEY GEN.": "ATTORNEY GENERAL", "COMMISSIONER OF AG. & INDUSTRIES": "COMMISSIONER OF AGRICULTURE AND INDUSTRIES", "COMM OF AGRICULTURE AND INDUSTRIES": "COMMISSIONER OF AGRICULTURE AND INDUSTRIES", "AGRICULTURE COMM.": "COMMISSIONER OF AGRICULTURE AND INDUSTRIES", "US SENATOR": "UNITED STATES SENATOR"}.get(name, name)
    if name in STATEWIDE:
        return STATEWIDE[name], None
    if name.startswith("PRESIDENT AND VICE"):
        return "president", None
    if name == "UNITED STATES SENATOR":
        return "senate", None
    if matched := re.match(r"UNITED STATES REPRESENTATIVE, (\d+)\w* CONGRESSIONAL", name):
        return "house", int(matched[1])
    if matched := re.match(r"STATE SENATOR, DISTRICT NO\. (\d+)", name):
        return f"senate:{int(matched[1])}:g", None
    if matched := re.match(r"STATE REPRESENTATIVE, DISTRICT NO\. (\d+)", name):
        return f"house:{int(matched[1])}:g", None
    if matched := re.match(r"STATE SENATE, DIST[IR]+CT 0*(\d+)$", name):
        return f"senate:{int(matched[1])}:g", None
    if matched := re.match(r"STATE HOUSE OF REPRESENTATIVES?, DISTRICT 0*(\d+)$", name):
        return f"house:{int(matched[1])}:g", None
    if matched := re.match(r"PUBLIC SERVICE COMMISSION, PLACE #(\d+)$", name):
        return f"public-service-commissioner:place-{matched[1]}", None
    if matched := re.match(r"UNITED STATES REPRESENTATIVE D(\d+)$", name):
        return "house", int(matched[1])
    if matched := re.match(r"STATE SENATOR D\d+ DISTRICT (\d+)$", name):
        return f"senate:{int(matched[1])}:g", None
    if matched := re.match(r"STATE REPRESENTATIVE D\d+ DISTRICT (\d+)$", name):
        return f"house:{int(matched[1])}:g", None
    if matched := re.match(r"US REP[,.] DIST\.? ?(\d+)$", name):
        return "house", int(matched[1])
    if matched := re.match(r"STATE SEN\.? DIST\.? ?(\d+)$", name):
        return f"senate:{int(matched[1])}:g", None
    if matched := re.match(r"STATE REP\.? (?:DIST ?)?(\d+)$", name):
        return f"house:{int(matched[1])}:g", None
    if matched := re.match(r"PUBLIC SERVICE (?:COMMISSION,? (?:PLACE|PL)\.? (?:NO\. )?|PL ?)(\d+)", name):
        return f"public-service-commissioner:place-{matched[1]}", None
    return None


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    records: list[tuple[date, str, str, str, str, str, str]] = []
    for day, url in FILES.items():
        if url.endswith(".xls"):
            book = pl.read_excel(raw.cached(SOURCE, url).path, has_header=False, infer_schema_length=0, sheet_id=0, raise_if_empty=False)
            for sheet, frame in book.items():
                rows = [[cell or "" for cell in row] for row in frame.iter_rows()]
                header = next((i for i, row in enumerate(rows) if row[0].strip() == "County Code"), None)
                if header is None or header == 0:
                    continue
                county = re.sub(r"[^a-z]", "", sheet.lower())
                names = rows[header - 1]
                for row in rows[header + 1 :]:
                    if len(row) < 7 or contest(row[2]) is None:
                        continue
                    for position in range(6, min(len(row), len(names))):
                        if names[position].strip() and not re.search(TOTALS, names[position].upper()):
                            records.append((day, county, row[2], names[position], row[4], row[position], row[3]))
            continue
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            for member in archive.namelist():
                if member.lower().endswith(".csv"):
                    county = re.sub(r"[^a-z]", "", Path(member).stem.lower())
                    table = pl.read_csv(archive.read(member), infer_schema=False, truncate_ragged_lines=True)
                    for row in table.iter_rows(named=True):
                        if row["Contest Title"] and contest(row["Contest Title"]) is not None:
                            records.append((day, county, row["Contest Title"].strip(), row["Precinct Name"].strip(), row["Candidate Name"].strip(), row["Registered Voters"], row["Party Code"]))
                    continue
                if not member.lower().endswith((".xls", ".xlsx")) or member.startswith("__MACOSX"):
                    continue
                county = re.sub(r"[^a-z]", "", re.sub(r"(?i)^2014-general-|[ _-]*2014 general precinct", "", Path(member).stem).lower()).removesuffix("xls").removesuffix("gen").replace("radolph", "randolph")
                content = archive.read(member)
                if content.lstrip(b"\xef\xbb\xbf").startswith(b"<?xml"):
                    sheets = xml_sheets(content)
                else:
                    try:
                        book = pl.read_excel(
                            io.BytesIO(content), has_header=False, infer_schema_length=0, sheet_id=0, raise_if_empty=False
                        )
                    except Exception:
                        with tempfile.TemporaryDirectory() as folder:
                            (Path(folder) / "in.xls").write_bytes(content)
                            subprocess.run(
                                ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", folder, str(Path(folder) / "in.xls")],
                                check=True,
                                capture_output=True,
                                timeout=120,
                            )
                            book = pl.read_excel(
                                Path(folder) / "in.xlsx", has_header=False, infer_schema_length=0, sheet_id=0, raise_if_empty=False
                            )
                    sheets = [[[cell or "" for cell in row] for row in frame.iter_rows()] for frame in book.values()]
                for rows in sheets:
                    if not rows:
                        continue
                    if rows[0][0] in ("Contest Title", "Contest Title "):
                        precincts = rows[0][3:]
                        for row in rows[1:]:
                            if len(row) < 4 or contest(row[0]) is None:
                                continue
                            for precinct, value in zip(precincts, row[3:], strict=False):
                                if precinct and not re.search(TOTALS, precinct.upper()):
                                    records.append((day, county, row[0], precinct, row[2], value, row[1]))
                        continue
                    if rows[0][0] == "ContestTitle":
                        for row in rows[1:]:
                            if len(row) < 4 or contest(row[0]) is None:
                                continue
                            for precinct, value in zip(rows[0][3:], row[3:], strict=False):
                                if precinct and not re.search(TOTALS, precinct.upper()):
                                    records.append((day, county, row[0], precinct, row[1], value, ""))
                        continue
                    if rows[0][0].strip() == "" and len(rows) > 2 and rows[1] and rows[1][0] != "" and any(contest(label.strip()) for label in rows[0] if label.strip()):
                        offices = {}
                        owner = ""
                        for position, label in enumerate(rows[0]):
                            owner = label.strip() if label.strip() else owner
                            offices[position] = owner
                        for row in rows[2:]:
                            if not row or not row[0] or re.search(TOTALS, row[0].upper()):
                                continue
                            for position in range(1, min(len(row), len(rows[1]))):
                                if rows[1][position] and contest(offices[position]) is not None:
                                    records.append((day, county, offices[position], row[0], re.sub(r"\s*\([A-Z ]+\)\s*$", "", rows[1][position]), row[position], ""))
                        continue
                    if not rows[0][0].startswith(("FOR ", "PROPOSED ")) or contest(rows[0][0]) is None:
                        continue
                    labels = next((i for i, row in enumerate(rows) if "Total Votes" in row), None)
                    if labels is None:
                        continue
                    names, header = rows[labels - 1], rows[labels]
                    owner = ""
                    owners = []
                    for position in range(len(header)):
                        owner = names[position] if position < len(names) and names[position] else owner
                        owners.append(owner)
                    for row in rows[labels + 1 :]:
                        if not row or not row[0] or re.search(TOTALS, row[0].upper()):
                            continue
                        for position, label in enumerate(header):
                            if label == "Total Votes" and position < len(row):
                                records.append((day, county, rows[0][0], row[0], owners[position], row[position], ""))
    frame = pl.DataFrame(records, schema=["day", "county", "title", "place", "choice", "votes", "party"], orient="row", infer_schema_length=None)
    titles = {title: contest(title) for title in frame["title"].unique().to_list()}
    frame = frame.with_columns(
        chamber=pl.col("title").replace_strict({t: c[0] for t, c in titles.items() if c}, default=None),
        district=pl.col("title").replace_strict({t: c[1] for t, c in titles.items() if c}, default=None, return_dtype=pl.Int16),
        county_code=pl.col("county").replace_strict(counties, default=None),
        votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
    ).filter(pl.col("votes").is_not_null() & pl.col("chamber").is_not_null())
    lost = frame.filter(pl.col("county_code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    frame = (
        frame.with_columns(
            candidate=pl.col("choice").str.to_uppercase().str.replace(r" ?/ ?.*$", "").str.strip_chars(),
            place=pl.col("place").str.replace_all("\x00", "").str.strip_chars(),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            kind=pl.when(pl.col("place").str.to_uppercase().str.contains(COUNTYWIDE))
            .then(pl.lit("county"))
            .otherwise(pl.lit("precinct")),
        )
        .with_columns(
            candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"),
            code=pl.when(pl.col("kind") == "county")
            .then("county_code")
            .otherwise(
                pl.concat_str(
                    pl.col("day").cast(pl.String), pl.lit("AL"), "county_code", pl.col("county").str.to_uppercase(), "place", separator=":"
                )
            ),
        )
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "district", "kind", "code", "county_code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("AL"),
            source=pl.lit("AL"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.filter(pl.col("kind") == "precinct").group_by("year").agg(pl.col("code").n_unique()).sort("year").rows())


def emit() -> Path:
    year = int(os.environ["AL_YEAR"])
    frame = pl.read_parquet(PARSED / "precincts.parquet").filter(pl.col("year") == year)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Alabama's {year} general election by precinct (Secretary of State)",
            polity="US-AL",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for day, url in FILES.items():
            if day.year == year:
                cs.artifact(raw.cached(SOURCE, url), source)
        cs.scope_units("us.precinct", "____-__-__:AL:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '01%%'"
            ).fetchall()
        }
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                     and coalesce(r.completeness, 1) >= 0.98
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   where x.scheme = 'us.contest' and x.value like %s""",
                [SOURCE, f"{year}-%%-%%:AL:%%"],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("AL"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            ).is_in(list(filled))
        )
        units = (
            frame.filter(pl.col("kind") == "precinct")
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("county_code").first(), pl.col("day").min())
            .sort("code")
        )
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String),
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county_code").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("county_code").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        medsl.attach(
            cs,
            conn,
            frame.drop("county_code"),
            {"AL": source},
            grain="precinct",
            partial="the Secretary of State's file has no precinct results for some counties",
        )
        return cs.write()
