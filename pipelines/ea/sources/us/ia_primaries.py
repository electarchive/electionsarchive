import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ia_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = Path.home() / "Downloads"
BASE = "https://sos.iowa.gov/elections/pdf/{year}/primary/canvsummary.pdf"
FILES = {
    2018: (date(2018, 6, 5), "ia_2018_pri_canvsummary.pdf"),
    2020: (date(2020, 6, 2), "ia_2020_pri_canvsummary.pdf"),
    2022: (date(2022, 6, 7), "ia_2022_pri_canvsummary.pdf"),
}
PARTIES = {
    "Rep.": "republican",
    "Dem.": "democratic",
    "Republican": "republican",
    "Democratic": "democratic",
    "Democrat": "democratic",
    "Libertarian": "libertarian",
}
PUBLISHER = "Iowa Secretary of State"
TITLE = "Primary election canvass summary"
TITLE_LINE = re.compile(r"^\s+(United States Senator|United States Representative District (\d+)) - (\S+)\s*$")
COUNTY_ROW = re.compile(r"^\s?([A-Z][A-Za-z.' ]+?)\s{2,}([\d,]+(?:\s+[\d,]+)+)\s*$")
ROW = re.compile(r"^\s*(?:(\S.*?)\s{2,})?Total\s+([\d,]+(?:\s+[\d,]+)*)\s*$")


def fetch() -> None:
    for year, (_, name) in FILES.items():
        url = BASE.format(year=year)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / name
            if path.exists():
                raw.store(
                    SOURCE,
                    url,
                    path.read_bytes(),
                    suffix=".pdf",
                    retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                    media_type="application/pdf",
                )


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%' order by u.code"""
            ).fetchall()
        }
    rows = []
    for year, (day, _) in FILES.items():
        try:
            path = raw.cached(SOURCE, BASE.format(year=year)).path
        except LookupError:
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        lines = text.replace("\f", "\n").splitlines()
        contest = None
        seen: set[tuple[tuple[str, str, int | None] | None, str]] = set()
        names: list[str] = []
        county = None
        for i, line in enumerate(lines):
            if line.strip().startswith(("2022 Primary", "Primary Election", "PRIMARY ELECTION")):
                contest = None
                continue
            found = TITLE_LINE.match(line)
            if found is not None:
                party = PARTIES.get(found[3])
                contest = None
                if party is not None:
                    contest = ("senate" if found[2] is None else "house", party, int(found[2]) if found[2] else None)
                    header = []
                    for later in lines[i + 1 :]:
                        if year != 2018 and "Election Day" in later:
                            break
                        if later.strip():
                            header.append(later)
                        if year == 2018 and "Over Votes" in later:
                            break
                    if year == 2018:
                        stacked = sorted(
                            (h for h in header if "Write-in Candidate" not in h), key=lambda h: len(h) - len(h.lstrip())
                        )
                        tokens = " ".join(h.split("Write-in")[0] for h in stacked).split()
                        names = []
                        while tokens:
                            take = 3 if len(tokens) > 2 and re.fullmatch(r"[A-Z]\.", tokens[1]) else 2
                            names.append(" ".join(tokens[:take]).upper())
                            tokens = tokens[take:]
                    else:
                        joined = re.sub(r"Write-in Under Votes Over Votes\s+Total", "", " ".join(header))
                        joined = re.sub(r"\b(REP|DEM|LIB|GRN|IND|NPA|NP)\b", "", joined)
                        names = [" ".join(n.split()).upper() for n in joined.split(",") if n.strip()]
                county = None
                continue
            if contest is None:
                continue
            if year == 2018:
                row = COUNTY_ROW.match(line)
                if row is None:
                    continue
                county, values = row[1], [int(v.replace(",", "")) for v in row[2].split()]
                if (contest, county) in seen:
                    continue
                seen.add((contest, county))
            else:
                found_county = re.match(r"^\s?(\S.*?)\s+(?:Election Day|Absentee)", line)
                if found_county:
                    county = found_county[1]
                    continue
                row = ROW.match(line)
                if row is None or county is None:
                    continue
                values = [int(v.replace(",", "")) for v in row[2].split()]
            key = re.sub(r"[^a-z]", "", county.lower())
            county = None
            if len(values) != len(names) + 4 or key not in fips:
                continue
            for candidate, votes in zip([*names, "WRITE-INS"], values[: len(names) + 1], strict=True):
                rows.append(
                    (
                        year,
                        f"{contest[0]}:{contest[1]}",
                        day,
                        False,
                        "IA",
                        contest[2],
                        fips[key],
                        candidate,
                        1,
                        candidate == "WRITE-INS",
                        votes,
                    )
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("IA")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 2018, 2020, and 2022 U.S. Senate and House primaries by county",
            polity="US-IA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.iowa.gov/elections/results/index.html",
            license="Public record",
        )
        for year in FILES:
            try:
                cs.artifact(raw.cached(SOURCE, BASE.format(year=year)), source)
            except LookupError:
                continue
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IA": source}, strict=True)
        return cs.write()
