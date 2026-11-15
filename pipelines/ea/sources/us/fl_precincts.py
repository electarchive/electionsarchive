import io
import os
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import shapefile
from access_parser import AccessParser

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.fl_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "precincts-fl"
PUBLISHER = "Florida Division of Elections"
TITLE = "Precinct-Level Election Results"
LANDING = "https://dos.fl.gov/elections/data-statistics/elections-data/precinct-level-election-results/"
LEGISLATURE = "Florida Legislature"
PLAN = "2000 General Election Precinct Plan (precinct00_pl)"
PLAN_URL = "http://www.leg.state.fl.us/data/redistricting_data/ArcViewShapfiles/precinct00_pl.zip"
SENATE = "The Florida Senate"
RETURNS = "2012 Redistricting Preclearance Submission, 2008 Election Returns"
RETURNS_URL = "https://www.flsenate.gov/UserContent/Session/Redistricting/20120312Preclearance/Election%20Returns/2008%20Election%20Returns.zip"
PRESIDENT_2000 = {
    "R": "GEORGE W. BUSH",
    "D": "AL GORE",
    "GRE": "RALPH NADER",
    "REF": "PAT BUCHANAN",
    "LIB": "HARRY BROWNE",
    "LAW": "JOHN HAGELIN",
    "WWP": "MONICA MOOREHEAD",
    "CPF": "HOWARD PHILLIPS",
    "SPF": "DAVID MCREYNOLDS",
    "SWP": "JAMES HARRIS",
    "WRI": "WRITE-INS",
}
FILES = {
    date(2012, 11, 6): "https://dos.fl.gov/media/697204/precinctlevelelectionresults2012gen.zip",
    date(2014, 11, 4): "https://dos.fl.gov/media/697201/precinctlevelelectionresults2014gen.zip",
}
OFFICES = {
    "PRESIDENT OF THE UNITED STATES": "president",
    "UNITED STATES SENATOR": "senate",
    "GOVERNOR": "governor",
    "ATTORNEY GENERAL": "attorney-general",
    "CHIEF FINANCIAL OFFICER": "chief-financial-officer",
    "COMMISSIONER OF AGRICULTURE": "commissioner-of-agriculture",
}
COLUMNS = [
    "abbrv",
    "county",
    "election",
    "date",
    "name",
    "number",
    "place",
    "registered",
    "a",
    "b",
    "c",
    "office",
    "district",
    "office_code",
    "choice",
    "party",
    "d",
    "candidate_id",
    "votes",
]


def fetch() -> None:
    raw.fetch(SOURCE, PLAN_URL, suffix=".zip")
    raw.fetch(SOURCE, RETURNS_URL, suffix=".zip")
    for url in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / url.rsplit("/", 1)[1]).read_bytes(),
                suffix=".zip",
                retrieved_at=datetime(2026, 10, 24, tzinfo=UTC),
                media_type="application/zip",
            )


def parse() -> None:
    with connect() as conn:
        labels: dict[str, str] = dict(
            conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '12%%'"""
            ).fetchall()
        )
    counties = {
        re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code for code, text in labels.items()
    }
    frames = []
    for day, url in FILES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            for member in archive.namelist():
                if not member.lower().endswith(".txt") or "PctResults" not in member:
                    continue
                frames.append(
                    pl.read_csv(
                        archive.read(member),
                        separator="\t",
                        has_header=False,
                        new_columns=COLUMNS,
                        infer_schema=False,
                        quote_char=None,
                        encoding="utf8-lossy",
                        truncate_ragged_lines=True,
                    ).with_columns(day=pl.lit(day))
                )
    frame = pl.concat(frames)
    office = pl.col("office").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase()
    district = pl.col("district").str.extract(r"(\d+)", 1).cast(pl.Int16)
    frame = (
        frame.with_columns(
            office=office,
            district=district,
            code=pl.col("county")
            .str.to_lowercase()
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(counties, default=None),
            place=pl.col("place").fill_null("").str.replace_all("\x00", "").str.strip_chars(),
        )
        .with_columns(
            chamber=pl.when(pl.col("office").is_in(["U.S. REPRESENTATIVE", "REPRESENTATIVE IN CONGRESS"]))
            .then(pl.lit("house"))
            .when(pl.col("office") == "STATE SENATOR")
            .then(pl.concat_str(pl.lit("senate:"), pl.col("district").cast(pl.String), pl.lit(":g")))
            .when(pl.col("office") == "STATE REPRESENTATIVE")
            .then(pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String), pl.lit(":g")))
            .when(pl.col("office").str.contains(r"^GOVERNOR AND"))
            .then(pl.lit("governor"))
            .otherwise(pl.col("office").replace_strict(OFFICES, default=None))
        )
        .filter(pl.col("chamber").is_not_null() & pl.col("votes").is_not_null())
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost['county'].unique().to_list()}")
    frame = (
        frame.with_columns(
            candidate=pl.col("choice")
            .str.to_uppercase()
            .str.replace(r" ?/ ?.*$", "")
            .str.strip_chars()
            .replace({"NUMBER OF UNDER VOTES": "UNDERVOTES", "TIMES OVER VOTED": "OVERVOTES"}),
            votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            county_code=pl.col("code"),
            code=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("FL"),
                "code",
                pl.col("county").str.to_uppercase(),
                "place",
                separator=":",
            ),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("day", "chamber", "district", "code", "county_code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("FL"),
            kind=pl.lit("precinct"),
            source=pl.lit("FL"),
            district=pl.when(pl.col("chamber") == "house").then("district"),
        )
    )
    frame = pl.concat(
        [frame, legislature(frame.schema), president_2008(frame.schema, counties, labels)], how="diagonal_relaxed"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("year").agg(pl.col("code").n_unique()).rows())


def legislature(schema: pl.Schema) -> pl.DataFrame:
    day = date(2000, 11, 7)
    with zipfile.ZipFile(raw.cached(SOURCE, PLAN_URL).path) as archive:
        reader = shapefile.Reader(dbf=io.BytesIO(archive.read("precinct00_pl.dbf")))
        names = [field[0] for field in reader.fields[1:]]
        rows = []
        for record in reader.iterRecords():
            row = {name: str(value).strip() for name, value in zip(names, record or [], strict=True)}
            total = int(row["G00PRE_V"])
            if not total:
                continue
            county = row["FLID"][:5]
            code = f"{day}:FL:{county}:{row['COUNTY'].upper()}:{row['PRECINCT']}"
            counted = 0
            for party, candidate in PRESIDENT_2000.items():
                share = float(row[f"G00PRE_{party}"]) * total / 100
                if abs(share - round(share)) > 0.01:
                    raise ValueError(f"{code} {party}: {share} is not a whole count")
                counted += round(share)
                rows.append((code, county, candidate, party == "WRI", round(share)))
            if counted != total:
                raise ValueError(f"{code}: {counted} against {total}")
    frame = pl.DataFrame(
        rows, schema=["code", "county_code", "candidate", "writein", "votes"], orient="row"
    ).with_columns(
        day=pl.lit(day),
        chamber=pl.lit("president"),
        district=pl.lit(None, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        year=pl.lit(day.year, pl.Int32),
        special=pl.lit(False),
        state=pl.lit("FL"),
        kind=pl.lit("precinct"),
        source=pl.lit("FL"),
    )
    if frame["code"].n_unique() * len(PRESIDENT_2000) != frame.height:
        raise ValueError("duplicate precinct codes in precinct00_pl")
    return frame.select([pl.col(name).cast(dtype) for name, dtype in schema.items()])


def president_2008(schema: pl.Schema, counties: dict[str, str], labels: dict[str, str]) -> pl.DataFrame:
    day = date(2008, 11, 4)
    rows = []
    for folder, counted in returns_2008().items():
        if folder in INCOMPLETE_2008:
            continue
        county = counties[re.sub(r"[^a-z]", "", folder.lower())]
        title = labels[county].upper()
        for (precinct, candidate), votes in counted.items():
            writein = candidate in medsl.POOLED or "WRITE" in candidate
            rows.append(
                (f"{day}:FL:{county}:{title}:{precinct}", county, "WRITE-INS" if writein else candidate, writein, votes)
            )
    frame = (
        pl.DataFrame(rows, schema=["code", "county_code", "candidate", "writein", "votes"], orient="row")
        .with_columns(measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16))
        .group_by("code", "county_code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            day=pl.lit(day),
            chamber=pl.lit("president"),
            district=pl.lit(None, pl.Int16),
            year=pl.lit(day.year, pl.Int32),
            special=pl.lit(False),
            state=pl.lit("FL"),
            kind=pl.lit("precinct"),
            source=pl.lit("FL"),
        )
    )
    return frame.select([pl.col(name).cast(dtype) for name, dtype in schema.items()])


def returns_2008() -> dict[str, dict[tuple[str, str], int]]:
    counties: dict[str, dict[tuple[str, str], int]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, RETURNS_URL).path) as archive:
        for member in sorted(archive.namelist(), key=lambda member: member.lower().endswith((".xls", ".pdf"))):
            parts = member.split("/")
            if len(parts) != 4 or parts[1] != "General" or not parts[3] or parts[2] in counties:
                continue
            county, name, data = parts[2], parts[3].lower(), archive.read(member)
            if name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    name, data = inner.namelist()[0].lower(), inner.read(inner.namelist()[0])
            if name.endswith((".asc", ".001")):
                counted = ess(data)
            elif data.startswith(b"precinct_id\t"):
                counted = canvass(data)
            elif name.endswith(".txt") and data.startswith(b'"'):
                counted = gems(data)
            elif name.endswith(".xls"):
                counted = sheet(data)
            elif name.endswith(".mdb"):
                counted = access(data)
            elif name.startswith("canvass report"):
                counted = report(data)
            else:
                continue
            if counted:
                counties[county] = counted
    return counties


INCOMPLETE_2008 = {"Alachua", "Indian River", "Palm Beach"}
TOKEN = re.compile(rb'"([^"]*)"|([^\s,"\']+)')
STATISTICS = {
    "TIMES BLANK VOTED": "UNDERVOTES",
    "NUMBER OF UNDER VOTES": "UNDERVOTES",
    "TIMES OVER VOTED": "OVERVOTES",
}


def ticket(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip().upper()
    if name.startswith("WRITE"):
        return name
    name = re.sub(r"^(OVER|UNDER) ?VOTES?$", r"\1VOTES", name)
    return re.sub(r"\bSTEVEN$", "STEVENS", re.sub(r"\s*(/|&|-| AND ).*$", "", name).strip())


def gems(data: bytes) -> dict[tuple[str, str], int]:
    types: dict[tuple[str, str], dict[str, int]] = {}
    for line in data.splitlines():
        if re.match(rb'"\w+ \d', line):
            line = line.strip()[1:-1].replace(b'""', b'"')
        fields = [(quoted or bare).decode("latin-1").strip() for quoted, bare in TOKEN.findall(line)]
        if len(fields) < 23 or not re.fullmatch(r"PRES[^-]*", fields[5].upper()) or fields[1] == "999999":
            continue
        candidate = fields[14].upper()
        if int(fields[13]) >= 999990:
            if candidate not in STATISTICS:
                continue
            candidate = STATISTICS[candidate]
        else:
            candidate = ticket(candidate)
        counts = types.setdefault((fields[1], candidate), {})
        counts[fields[20]] = counts.get(fields[20], 0) + int(fields[22] or 0)
    return {key: counts.get("Total", sum(counts.values())) for key, counts in types.items()}


def ess(data: bytes) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    for line in data.decode("latin-1").splitlines():
        if len(line) < 120 or not line[:17].isdigit():
            continue
        shift = 30 if line[17:47].isdigit() else 0
        if not line[27 + shift : 83 + shift].strip().upper().startswith("PRES"):
            continue
        candidate = ticket(line[83 + shift : 121 + shift])
        key = (str(int(line[7:11])), candidate)
        counted[key] = counted.get(key, 0) + int(line[11:17])
    return counted


def sheet(data: bytes) -> dict[tuple[str, str], int]:
    try:
        frame = pl.read_excel(data, sheet_name="President", has_header=False, infer_schema_length=0)
    except ValueError:
        return {}
    rows = frame.rows()
    header = next(index for index, row in enumerate(rows) if row[0] == "Prec.")
    names = [ticket(str(name)) for name in rows[header][1:]]
    counted: dict[tuple[str, str], int] = {}
    for row in rows[header + 1 :]:
        if row[0] is None or row[0].strip().upper() == "TOTALS":
            continue
        for candidate, votes in zip(names, row[1:], strict=True):
            counted[(row[0].strip(), candidate)] = int(float(votes or 0))
    return counted


def access(data: bytes) -> dict[tuple[str, str], int]:
    with tempfile.NamedTemporaryFile(suffix=".mdb") as file:
        file.write(data)
        file.flush()
        table = AccessParser(file.name).parse_table("totals")
    counted: dict[tuple[str, str], int] = {}
    for precinct, votes, race, candidate in zip(
        table["PRECINCT"], table["TOTALS"], table["RACENAME"], table["CANDNAME"], strict=True
    ):
        if not race.upper().startswith("PRES"):
            continue
        candidate = candidate.upper()
        if candidate in STATISTICS:
            candidate = STATISTICS[candidate]
        elif candidate.startswith("TIMES "):
            continue
        else:
            candidate = ticket(candidate)
        key = (str(int(precinct)), candidate)
        counted[key] = counted.get(key, 0) + int(votes)
    return counted


def report(data: bytes) -> dict[tuple[str, str], int]:
    text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True).stdout
    lines = [line.rstrip() for line in text.decode("utf-8").splitlines()]
    counted: dict[tuple[str, str], int] = {}
    for index, line in enumerate(lines):
        if line.strip() != "President":
            continue
        unit = next(text.strip() for text in reversed(lines[:index]) if text.strip())
        for row in lines[index + 2 :]:
            match = re.fullmatch(r"(\S.*?)\s{2,}[\d,\s]*?([\d,]+)", row)
            if match is None:
                break
            counted[(unit, ticket(match[1]))] = int(match[2].replace(",", ""))
    return counted


def canvass(data: bytes) -> dict[tuple[str, str], int]:
    frame = pl.read_csv(data, separator="\t", infer_schema=False, quote_char=None, encoding="utf8-lossy")
    counted: dict[tuple[str, str], int] = {}
    for precinct, contest, candidate, total in frame.select(
        "precinct_id", "contest_name", "candidate_name", "grand_total"
    ).iter_rows():
        if not contest.upper().startswith("PRES") or not total:
            continue
        key = (precinct, ticket(candidate))
        counted[key] = counted.get(key, 0) + int(total)
    return counted


def emit() -> Path:
    year = int(os.environ["FL_YEAR"])
    frame = pl.read_parquet(PARSED / "precincts.parquet").filter(pl.col("year") == year)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary={
                2000: "Florida's 2000 presidential election by precinct, from the Legislature's precinct plan",
                2008: "Florida's 2008 presidential election by precinct, from the county returns in the Senate's 2012 preclearance submission",
            }.get(
                year,
                "Florida's presidential, federal, statewide, and legislative general elections by precinct, 2012 and 2014",
            ),
            polity="US-FL",
        )
        publisher, title, landing, urls = {
            2000: (LEGISLATURE, PLAN, PLAN_URL, [PLAN_URL]),
            2008: (SENATE, RETURNS, RETURNS_URL, [RETURNS_URL]),
        }.get(year, (PUBLISHER, TITLE, LANDING, [url for day, url in FILES.items() if day.year == year]))
        source = cs.source(publisher, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=landing,
                license="Public record",
            )
        for url in urls:
            cs.artifact(raw.cached(SOURCE, url), source)
        cs.scope_units("us.precinct", "____-__-__:FL:%")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '12%%'"
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
                   where x.scheme = 'us.contest' and x.value ~ %s""",
                [SOURCE, f"^{year}-..-..:FL:"],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("FL"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            ).is_in(list(filled))
        )
        units = (
            frame.group_by("code")
            .agg(pl.col("state").first(), pl.col("county_code").first(), pl.col("day").min())
            .sort("code")
        )
        cs.frame(
            "reporting_unit",
            units.select(
                id=pl.struct("code", "day").map_elements(
                    lambda row: str(cs.unit("us.precinct", row["code"], row["day"])), return_dtype=pl.String
                ),
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county_code").replace_strict(
                    {code: str(unit) for code, (unit, _) in counties.items()}
                ),
                area_relation=pl.lit("within"),
                area_id=pl.col("county_code").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        medsl.attach(cs, conn, frame.drop("county_code"), {"FL": source}, grain="precinct")
        return cs.write()
