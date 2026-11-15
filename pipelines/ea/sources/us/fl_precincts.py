import html
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
RETURNS_2004 = "2012 Redistricting Preclearance Submission, 2004 Election Returns"
RETURNS_2004_URL = RETURNS_URL.replace("2008", "2004")
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
    raw.fetch(SOURCE, RETURNS_2004_URL, suffix=".zip")
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
        [
            frame,
            legislature(frame.schema),
            president(
                frame.schema,
                counties,
                labels,
                date(2008, 11, 4),
                {folder: counted for folder, counted in returns_2008().items() if folder not in INCOMPLETE_2008},
            ),
            president(frame.schema, counties, labels, date(2004, 11, 2), returns_2004()),
        ],
        how="diagonal_relaxed",
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


def president(
    schema: pl.Schema,
    counties: dict[str, str],
    labels: dict[str, str],
    day: date,
    returns: dict[str, dict[tuple[str, str], int]],
) -> pl.DataFrame:
    rows = []
    for folder, counted in returns.items():
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
        for member in sorted(archive.namelist(), key=lambda member: member.lower().endswith(".xls")):
            parts = member.split("/")
            if len(parts) != 4 or parts[1] != "General" or not parts[3] or parts[2] in counties:
                continue
            county, name, data = parts[2], parts[3].lower(), archive.read(member)
            if name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    name, data = inner.namelist()[0].lower(), inner.read(inner.namelist()[0])
            if name.endswith((".asc", ".001")):
                counted = ess(data)
            elif name.endswith(".txt") and data.startswith(b'"'):
                counted = gems(data)
            elif name.endswith(".xls"):
                counted = sheet(data)
            elif name.endswith(".mdb"):
                counted = access(data)
            else:
                continue
            if counted:
                counties[county] = counted
    return counties


INCOMPLETE_2008 = {"Alachua"}
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
                2004: "Florida's 2004 presidential election by precinct, from the county returns in the Senate's 2012 preclearance submission",
                2008: "Florida's 2008 presidential election by precinct, from the county returns in the Senate's 2012 preclearance submission",
            }.get(
                year,
                "Florida's presidential, federal, statewide, and legislative general elections by precinct, 2012 and 2014",
            ),
            polity="US-FL",
        )
        publisher, title, landing, urls = {
            2000: (LEGISLATURE, PLAN, PLAN_URL, [PLAN_URL]),
            2004: (SENATE, RETURNS_2004, RETURNS_2004_URL, [RETURNS_2004_URL]),
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


NOMINEES = {
    "BUSH": "GEORGE W. BUSH",
    "CHENEY": "GEORGE W. BUSH",
    "KERRY": "JOHN F. KERRY",
    "PEROUTKA": "MICHAEL A. PEROUTKA",
    "BALDWIN": "MICHAEL A. PEROUTKA",
    "BADNARIK": "MICHAEL BADNARIK",
    "COBB": "DAVID COBB",
    "HARRIS": "JAMES HARRIS",
    "BROWN": "WALTER F. BROWN",
    "NADER": "RALPH NADER",
    "NADAR": "RALPH NADER",
    "CAMEJO": "RALPH NADER",
    "EDWARDS": "JOHN F. KERRY",
    "CAMPAGNA": "MICHAEL BADNARIK",
    "LAMARCHE": "DAVID COBB",
    "TROWE": "JAMES HARRIS",
    "HERBERT": "WALTER F. BROWN",
    "WRITE": "WRITE-INS",
    "OVER": "OVERVOTES",
    "UNDER": "UNDERVOTES",
    "BLANK": "UNDERVOTES",
}
SURNAME = re.compile(rf"\b({'|'.join(NOMINEES)})", re.IGNORECASE)
NUMBER = re.compile(r"^-?[\d,]+(\.0+)?$")
CELL = re.compile(r"\S+(?:(?<!Number)(?<!\)) \S+)*")
PRIMARY = re.compile(r"\b(BRAUN|CLARK|DEAN|GEPHA?RDT|KUCINICH|LIE?BE?RMAN|SHARPTON)\b", re.IGNORECASE)
CONTEST = re.compile(r"SENAT|CONGRESS|REPRESENTATIVE|AMENDMENT", re.IGNORECASE)
PARTY = re.compile(
    r"\s*(Republican|Democrat|Libertarian|Green|Reform|Socialist|Constitution|Independent|Party)", re.IGNORECASE
)
SUMMARY = re.compile(r"\b(SUB)?TOTALS?\b|^COUNT$|^CUMULATIVE|^RACE %|^%|^COUNTY ?WIDE|^JURISDICTION", re.IGNORECASE)


def nominee(text: str) -> str | None:
    found = SURNAME.search(text)
    return NOMINEES[found.group(1).upper()] if found else None


def count(text: str) -> int | None:
    text = text.strip()
    return int(float(text.replace(",", ""))) if NUMBER.match(text) else None


def layout(data: bytes) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=data, check=True, capture_output=True
    ).stdout.decode("latin-1")


def cells(data: bytes) -> list[list[list[str]]]:
    text = data.decode("latin-1")
    tables = []
    for table in re.findall(r"(?is)<table.*?</table>", text):
        rows = []
        for row in re.findall(r"(?is)<tr.*?(?=<tr|</table)", table):
            rows.append(
                [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                    for cell in re.findall(r"(?is)<t[dh][^>]*>(.*?)(?=<t[dh]|</tr|$)", row)
                ]
            )
        tables.append(rows)
    return tables


def sheets(data: bytes) -> list[list[list[str]]]:
    frames = pl.read_excel(data, sheet_id=0, has_header=False, infer_schema_length=0, raise_if_empty=False)
    return [[["" if cell is None else str(cell) for cell in row] for row in frame.rows()] for frame in frames.values()]


def wide(rows: list[list[str]], label: int = 0, carry: bool = False) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    columns: dict[int, str] = {}
    precinct = ""
    for row in rows:
        named: dict[int, str] = {}
        for index, cell in enumerate(row):
            name = nominee(cell)
            if index != label and name and name not in named.values():
                named[index] = name
        if {"GEORGE W. BUSH", "JOHN F. KERRY"} <= set(named.values()) or (
            len(named) >= 2 and label < len(row) and re.match(r"PRES", row[label], re.IGNORECASE)
        ):
            first = min(named)
            columns = {}
            while first in named:
                columns[first] = named[first]
                first += 1
            continue
        if not columns:
            continue
        figures = {index: count(row[index]) if index < len(row) else None for index in columns}
        if all(re.fullmatch(r"\(?[A-Za-z]{3}\)?|", row[index].strip()) for index in columns if index < len(row)):
            continue
        if any(figure is None for figure in figures.values()):
            if any(index < len(row) and row[index].strip() and figures[index] is None for index in columns):
                columns = {}
            continue
        name = row[label].strip() if label < len(row) else ""
        if carry and not name:
            name = precinct
        precinct = name
        if not name or SUMMARY.search(name):
            continue
        for index, candidate in columns.items():
            counted[(name, candidate)] = counted.get((name, candidate), 0) + (figures[index] or 0)
    return counted


def tall(rows: list[list[str]]) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    header: list[str] = []
    seen: set[str] = set()
    if any(re.match(r"(August|September|.*Primary)", cell) for row in rows[:5] for cell in row):
        return counted
    for row in rows:
        labels = [cell for cell in row if cell.strip() and count(cell) is None]
        filled = sum(1 for cell in row[1:] if cell.strip())
        if (
            len(set(labels)) >= 3 or filled >= 3 and re.match(r"CANDIDATE|PRECINCT|OFFICE|PRES", row[0], re.IGNORECASE)
        ) and not any(nominee(cell) for cell in row[:2]):
            header, seen = [] if CONTEST.search(row[0]) else row, set()
            continue
        start = next((index for index, cell in enumerate(row) if cell.strip()), None)
        if start is None or not header:
            continue
        candidate = nominee(row[start])
        if candidate is None or candidate in seen and candidate not in ("WRITE-INS",):
            if (
                candidate is None
                and labels
                and (seen and not PARTY.match(row[start]) or PRIMARY.search(row[start]) or CONTEST.search(row[start]))
            ):
                header = []
            continue
        seen.add(candidate)
        name = ""
        for index in range(start + 1, len(row)):
            if count(row[index]) is None:
                name = ""
                continue
            name = header[index].strip() if index < len(header) else ""
            if name and not SUMMARY.search(name):
                counted[(name, candidate)] = counted.get((name, candidate), 0) + (count(row[index]) or 0)
    return counted


def statement(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    for page in text.split("\f"):
        lines = page.splitlines()
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if re.match(r"\s*(Jurisdiction|County) Wide|Presidential$", line)
            ),
            None,
        )
        if start is None:
            start = max(
                (
                    index
                    for index, line in enumerate(lines[:30])
                    if re.search(r"Voters|Votes(?! Cast)|Voted|\([A-Za-z]{3}\)", line)
                ),
                default=None,
            )
        if start is None:
            continue
        head = [line for line in lines[: start + 1] if not re.search(r"Page:|Date:|Time:|SOVC|Statement of", line)]
        runs = [(found.start(), found.group()) for line in head for found in CELL.finditer(line)]
        groups = sorted({spot for spot, run in runs if run.startswith("Reg.")} | {0})
        titled = [
            spot + len(run) // 2
            for spot, run in runs
            if re.search(r"\bPRES", run, re.IGNORECASE) and not re.search(r"PREF|PRECINCT", run, re.IGNORECASE)
        ]
        top = next((index for index, line in enumerate(head) if "Reg." in line or SURNAME.search(line)), 0)
        edges = sorted({found.start() for line in head[top:] for found in CELL.finditer(line)})
        columns: dict[str, tuple[int, int, int, int]] = {}
        for low, high in zip(groups, [*groups[1:], 10**4], strict=True):
            if not any(low <= center < high for center in titled):
                continue
            for start_, run in runs:
                hits = [(start_ + word.start(), nominee(word.group())) for word in re.finditer(r"\S+", run)]
                hits = [(spot, name) for spot, name in hits if name]
                for rank, (spot, name) in enumerate(hits):
                    spot = start_ if rank == 0 else spot
                    if name and name not in ("OVERVOTES", "UNDERVOTES") and low <= spot < high and name not in columns:
                        columns[name] = (spot, min([edge for edge in edges if edge > spot + 1] + [high]), low, high)
        if PRIMARY.search(" ".join(head)) or not any(
            name in columns for name in ("GEORGE W. BUSH", "JOHN F. KERRY", "RALPH NADER", "WRITE-INS")
        ):
            continue
        for line in lines[start + 1 :]:
            if re.match(r"\s*(Total|Cumulative)\s*$", line):
                break
            found = re.match(r"^\s*(\S.*?)\s{2,}\d", line)
            if found is None or SUMMARY.search(found.group(1)):
                continue
            precinct = re.sub(r"\s+", " ", found.group(1))
            tail = line[found.end(1) :]
            tokens = [
                (token.end() + found.end(1), int(token.group().replace(",", "")))
                for token in re.finditer(r"(?<![\d.,])\d[\d,]*(?![\d.,%])", tail)
            ]
            pairs = [
                (token.end() + found.end(1), int(token.group().replace(",", "")))
                for token in re.finditer(r"(?<![\d.,])\d[\d,]*(?=\s+(?:\d+\.\d+%?|-)(?:\s|$))", tail)
            ]
            for name, (low, high, group, limit) in columns.items():
                rated = [figure for end, figure in pairs if group < end <= limit]
                if rated:
                    ranked = sorted(spot for spot, _, edge, _ in columns.values() if edge == group)
                    if len(rated) != len(ranked):
                        raise ValueError(f"{len(rated)} figures for {len(ranked)} columns: {line}")
                    figures = [rated[ranked.index(low)]]
                else:
                    figures = [figure for end, figure in tokens if low < end <= high]
                if len(figures) > 1:
                    raise ValueError(f"{name} {low}-{high}: {line}")
                if figures:
                    counted[(precinct, name)] = counted.get((precinct, name), 0) + figures[0]
    return counted


def numbered(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    keys: dict[str, str] = {}
    active = False
    for line in text.splitlines():
        if re.match(r"\s*PRESIDENT AND VICE", line):
            active = True
            continue
        if not active:
            continue
        pairs = re.findall(r"(\d\d) = (.+?)(?:\s{2,}|$)", line)
        if pairs:
            for key, name in pairs:
                keys[key] = nominee(name) or "WRITE-INS"
            keys = dict(sorted(keys.items()))
            continue
        found = re.match(r"^(\d{4}) (.*?)((?:\s+\d+)+)\s*$", line)
        if found and keys:
            figures = found.group(3).split()[-len(keys) :]
            for name, figure in zip(keys.values(), figures, strict=True):
                counted[(found.group(1), name)] = counted.get((found.group(1), name), 0) + int(figure)
        elif (
            re.match(r"\s*[A-Z][A-Z ]{6,}(\s{2,}WITH\b.*)?$", line)
            and keys
            and not re.match(r"\s*(PRECINCTS|NUMBERED|VOTES)", line)
        ):
            active = False
    return counted


def report(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    precinct, active, after = "", False, False
    for line in text.splitlines():
        if line.startswith("RUN DATE"):
            after = True
            continue
        if after and line.strip():
            precinct, after = re.sub(r"\s+", " ", line.strip()), False
            continue
        if re.match(r"\s*PRESIDENT AND VICE", line):
            active = True
            continue
        found = re.match(r"^\s+(\S.*?)[ .]*\.\s+([\d,]+)\b", line)
        if active and found:
            name = nominee(found.group(1))
            if name:
                counted[(precinct, name)] = counted.get((precinct, name), 0) + int(found.group(2).replace(",", ""))
        elif (
            active
            and re.match(r"\s*[A-Z][A-Z .]{6,}$", line)
            and not re.match(r"\s*\(?WITH|VOTE FOR", line, re.IGNORECASE)
        ):
            active = False
    return counted


def grid(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    header: list[str] = []
    active = False
    for line in text.splitlines():
        if line.startswith("CANDIDATE"):
            header = re.split(r"\s{2,}", line.strip())[1:]
            continue
        found = re.match(r"^(\S.*?)\s{2,}([\d\s]+)$", line)
        if found is None:
            if line.strip():
                active = bool(re.match(r"PRES", line))
            continue
        name = nominee(found.group(1))
        if not active or name is None:
            continue
        figures = found.group(2).split()
        if len(figures) != len(header):
            raise ValueError(f"{len(figures)} figures for {len(header)} precincts: {line}")
        for precinct, figure in zip(header, figures, strict=True):
            if not SUMMARY.search(precinct):
                counted[(precinct, name)] = counted.get((precinct, name), 0) + int(figure)
    return counted


def unwrapped(text: str) -> dict[tuple[str, str], int]:
    lines = [
        line.strip()[1:-1].replace('""', '"') if line.startswith('"duval,') else line for line in text.splitlines()
    ]
    return gems("\n".join(lines).encode("latin-1"))


def converted(data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "returns.doc"
        path.write_bytes(data)
        subprocess.run(
            ["soffice", f"-env:UserInstallation=file://{folder}/profile", "--headless", "--convert-to", "html"]
            + ["--outdir", folder, str(path)],
            check=True,
            capture_output=True,
        )
        return path.with_suffix(".html").read_bytes()


def carried(rows: list[list[str]]) -> dict[tuple[str, str], int]:
    return wide(rows, carry=True)


def listed(rows: list[list[str]]) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    for row in rows:
        if len(row) > 4 and re.match(r"PRES", row[2] or "") and count(row[4] or "") is not None:
            name = nominee(row[3]) or row[3].upper()
            counted[(row[0], name)] = counted.get((row[0], name), 0) + (count(row[4]) or 0)
    return counted


def bracketed(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    keys: dict[str, str] = {}
    active = False
    columns: list[str] = []
    for line in text.splitlines():
        if re.match(r"\s*PRESIDENT AND VICE", line):
            active = True
            continue
        found = re.match(r"\s*\[(\d{4})\]\s+(.+?)\s{2,}", line)
        if found and active:
            if nominee(found.group(2)):
                keys[found.group(1)] = nominee(found.group(2)) or ""
            continue
        if re.match(r"\s*[A-Z]", line) and not re.match(r"\s*Precinct", line):
            active = False
        if re.match(r"\s*Precinct\s+\[", line):
            columns = re.findall(r"\[(\d{4})\]", line)
            continue
        row = re.match(r"^\s*(\S+)((?:\s+[\d,]+)+)\s*$", line)
        if row and columns and any(key in keys for key in columns):
            figures = row.group(2).split()
            if len(figures) != len(columns):
                raise ValueError(f"{len(figures)} figures for {len(columns)} columns: {line}")
            for key, figure in zip(columns, figures, strict=True):
                if key in keys:
                    counted[(row.group(1), keys[key])] = int(figure.replace(",", ""))
    return counted


def keyed(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    keys: dict[int, str] = {}
    active = False
    for line in text.splitlines():
        if re.match(r"\s*PRESIDENT AND VICE", line):
            active = True
            continue
        found = re.match(r"\s*(\d)\s+(\S.*?)\s{2,}[\d,]+\s+[\d.]+%", line)
        if active and found:
            keys[int(found.group(1))] = nominee(found.group(2)) or found.group(2).upper()
            continue
        row = re.match(r"^\s*(\d{4}|[A-Z][A-Za-z ]+?)\s+\d+\s+\d+\s+(?:[\d.]+%|N/A)((?:\s+\d+)+)\s*$", line)
        if active and row and keys and len(row.group(2).split()) >= max(keys):
            figures = row.group(2).split()
            for key, name in keys.items():
                counted[(row.group(1), name)] = int(figures[key - 1])
        elif keys and re.match(r"\s*[A-Z][A-Z ,.()-]{8,}$", line):
            active = False
    return counted


def paged(text: str) -> dict[tuple[str, str], int]:
    counted: dict[tuple[str, str], int] = {}
    for page in text.split("\f"):
        lines = page.splitlines()
        precinct = lines[2].split()[0] if len(lines) > 2 and lines[2].strip() else None
        active = False
        for line in lines:
            if re.match(r"PRESIDENT\b", line):
                active = True
                continue
            if re.match(r"[A-Z][A-Z .,&-]{5,}", line) and not nominee(line[:30]):
                active = False
            found = re.match(r"^(\S.*?)\s{2,}([\d,]+) +[\d.]+%", line)
            if active and precinct and found and nominee(found.group(1)):
                name = nominee(found.group(1)) or ""
                counted[(precinct, name)] = counted.get((precinct, name), 0) + int(found.group(2).replace(",", ""))
    return counted


TABLES_2004 = {
    "Bradford": ("2004 General.htm", wide),
    "Clay": ("ElectionData2004.zip!details/excelresults_pres.xls", wide),
    "Franklin": ("Franklin County Election Results 2004.xls", tall),
    "Gilchrist": ("elect gen044.xls", tall),
    "Hamilton": ("2004 General Election.doc", tall),
    "Hendry": ("2004General Election.htm", tall),
    "Highlands": ("General Election/GEN04 FINAL OFFICIAL.xls", carried),
    "Hillsborough": ("General04.xls", wide),
    "Jackson": ("nov04.htm", tall),
    "Jefferson": ("Jefferson County Florida Election Returns - Nov 2, 2004.htm", tall),
    "Liberty": ("General 11-2-04.doc", tall),
    "Madison": ("Madison%20Co%20Election%20Results.xls", tall),
    "Manatee": ("110204.xls", wide),
    "Martin": ("gen2004.xls", wide),
    "Orange": ("2004 General Election.xls", wide),
    "Palm Beach": ("GENERAL2004.xls", wide),
    "Pasco": ("PascoGEN04ResultsWithGroup.xls", listed),
    "Santa Rosa": ("2004 Election Results.xls", wide),
    "Sarasota": ("4 RESULTS GENERAL ELECTION 11-2-2004.xls", wide),
    "Sumter": ("General_11022004.xls", tall),
    "Suwannee": ("11-2-04 General Returns.doc", tall),
    "Taylor": ("GENERAL ELECTION 11-2-2004.xls", wide),
}

TEXTS_2004 = {
    "Alachua": ("SOVC_Nov04_Official.pdf", statement),
    "Bay": ("Bay County 2004 General Precinct results.HTM", numbered),
    "Brevard": ("g124abst.pdf", statement),
    "Broward": ("General_11022004.txt", report),
    "Charlotte": ("GEN2004.pdf", grid),
    "Citrus": ("Cit04gen.pdf", statement),
    "Dixie": ("general GEMS SOVC REPORT.pdf", statement),
    "Duval": ("DUV_general_2004.txt", unwrapped),
    "Escambia": ("2004_GENERAL.txt", numbered),
    "Hernando": ("2004GeneralOfficialResults.pdf", statement),
    "Indian River": ("20041102 (General Election Results).pdf", paged),
    "Lake": ("General Election Precinct Results, November 2, 2004.htm", numbered),
    "Lee": ("gendist04.pdf", numbered),
    "Leon": ("2004gsovc.pdf", statement),
    "Levy": ("General%20Election.pdf", statement),
    "Marion": ("MarionCounty2004GeneralCanvass.pdf", keyed),
    "Miami-Dade": ("canvas110204.pdf", bracketed),
    "Nassau": ("General_2004_Precinct_by_Precinct[2].pdf", numbered),
    "Okeechobee": ("General November 2, 2004.pdf", statement),
    "Osceola": ("General04osceola.pdf", statement),
    "Seminole": ("General 2004 Results.pdf", statement),
    "St. Lucie": ("Official Final Results 2004 General Precinct by Precinct.pdf", statement),
    "Volusia": ("General_11022004.pdf", statement),
    "Wakulla": ("Wakulla2004general.pdf", statement),
}


def member(archive: zipfile.ZipFile, county: str, name: str) -> tuple[bytes, str]:
    outer, _, inner = name.partition("!")
    data = archive.read(f"2004 Election Returns/{county}/{outer}")
    if inner:
        with zipfile.ZipFile(io.BytesIO(data)) as nested:
            data = nested.read(inner)
    return data, (inner or name).rsplit(".", 1)[1].lower()


def returns_2004() -> dict[str, dict[tuple[str, str], int]]:
    found: dict[str, dict[tuple[str, str], int]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, RETURNS_2004_URL).path) as archive:
        for county, (name, reader) in TABLES_2004.items():
            data, suffix = member(archive, county, name)
            found[county], seen = {}, []
            for table in (
                sheets(data)
                if suffix == "xls" and data[:4] == b"\xd0\xcf\x11\xe0"
                else cells(converted(data) if suffix == "doc" else data)
            ):
                counted = reader(table)
                totals: dict[str, int] = {}
                for (_, candidate), votes in counted.items():
                    totals[candidate] = totals.get(candidate, 0) + votes
                if totals not in seen:
                    seen.append(totals)
                    for key, votes in counted.items():
                        found[county][key] = found[county].get(key, 0) + votes
        for county, (name, reader) in TEXTS_2004.items():
            data, suffix = member(archive, county, name)
            found[county] = reader(
                layout(data) if suffix == "pdf" else html.unescape(re.sub(r"<[^>]+>", "", data.decode("latin-1")))
            )
    counties: dict[str, dict[tuple[str, str], int]] = {}
    for county, counted in found.items():
        counties[county] = {}
        for (precinct, candidate), votes in counted.items():
            key = (precinct, nominee(candidate) or candidate)
            counties[county][key] = counties[county].get(key, 0) + votes
    return counties
