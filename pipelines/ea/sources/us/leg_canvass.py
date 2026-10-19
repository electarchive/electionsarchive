import csv
import io
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fl_sos, medsl, nj_sos, openelections, wa_local
from ea.store import DATA, connect

SOURCE = "us.leg_canvass"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
IL_DOWNLOAD = "https://elections.il.gov/NewDocDisplay.aspx?{}"
IL_PUBLISHER = "Illinois State Board of Elections"
IL_TITLE = "Election vote totals"
IL_FILES = {
    2000: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZZw3h4ar3NE4CX%2bu5lLO94twHzufQ58vYiTTyLyUvgi0WN69Fo5kccZSf91zUmvfBCNfBz1%2bvUqcSf39ceMuxJo%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZZw3h4ar3NE4CX%2bu5lLO94s5VwxtnxnL3VC0goT8Xc4%2bG3Zqge0MjbTMcYXIVjA9aB4yqwXuBtcXtlF293qf5J59K%2bzE5ZZudA%3d%3d",
    ),
    2002: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeeBDZh5aFV%2f5Fj6mxlHEmXEac%2b7FDWzAeI%2beuD9AjYyH%2bPmg1%2bQ4lfkBdSpR8uWvkAJg0R2vg%2fFy4Up2m1z238%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeeBDZh5aFV%2f5Fj6mxlHEmUjDI54OVMzUYhwPAtDMfy7uhkFGT1gSQdFkKuerID6m6lECFSk0x08wjY3ECQLSqo6Kq4vaTa%2fQw%3d%3d",
    ),
    2004: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZbBAaynJaYDeehzrVcUaY%2f6dQuJ6YjQnX3Gmw0TVe3reHGtLNWadKq5zrwvfeidkANhJ4vyTbMREyO1kv0Kr9YA%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZbBAaynJaYDeehzrVcUaY%2f5m4n1hiFA9SAh0DW2mSW4lk4zv%2fR18pcpLsOTeXf803DU6FOI49Rp1fl7me8bTKE853oTvG4qEmw%3d%3d",
    ),
    2006: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZYCUKSb2ZB%2fCxykq03t7B1JOgGbXUywd8lSoSdcSn1NOnhz5S%2fCRQGD7GL0zriNUOSu%2bHWeIGLKfeqxdIlsdJDA%3d",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZYCUKSb2ZB%2fCxykq03t7B1Kk%2bqKdEb0phCJOtRg6etlxo1fdYpjrxkGmrCDhN7CW5EP1JRpu7oZ1nbpWicqSiSOiabnLVna8VQ%3d%3d",
    ),
    2008: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZVcE%2fKSRRVYPmEiR0E1M2978KTDoe9Wt%2by0Fu4gjQ7hcfhl%2f%2f1D5qId5Ex2btBAktyuUqbC3tmKh",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZVcE%2fKSRRVYPmEiR0E1M294%2fG9VxI%2byEMFthnmNvGlJJJja51WmVMkjm7RcEAZfrBIUuIX9bTpQn%2f8BWecZKQtA%3d",
    ),
    2010: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZchKLBtq0PZpEVaLjqq6OleE8DkYy3MmTpLOtdCJcZS1kVBslIYL%2f1tce8O8SBv3x2sNThWzTc4n",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZchKLBtq0PZpEVaLjqq6OlfG226W57pkA7fg3O957p7A0XrcyCBitToMPtN%2b1Igml9W8l4WzEDLTlrqV1KVDXVc%3d",
    ),
    2012: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Zer3TqjMLZFBpyGuzFVLVTjMzwFBag7lMBUEzMjHB2EUWqGr2rtkDCVng39K6blavo0Jt6fqxESB",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Zer3TqjMLZFBpyGuzFVLVTjvgR4qMJ4ABVTDfVQr7Rp1c3Xb8RLDa54SjTDUa20lB2YqR6MpDCzVELe7bcNocLk%3d",
    ),
    2014: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZasjAerqxs3fRrf2XYrBXtEbKmVLSjufoQA9F%2b6gOYWYukAdACVpfqNvXS55dDoOw6hcmTvo2Kjo",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZasjAerqxs3fRrf2XYrBXtGGnkcw1G96QzlBYJgOnLM7Ga8QRu3XuaFCd9MPCmYxKdHbwXUX35VkrGYmOkqck6M%3d",
    ),
    2016: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZcyCkMA7x3sXBKi6Ks4uZustlNRzKk7R7o%2fUN%2fUv1sMbaVKf25KY3N3OWQWoH4blCnfzDDDR5%2b%2fg",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZcyCkMA7x3sXBKi6Ks4uZuu%2fl7C4HQEu06X05BGg9mYw7wDdwyO07Bm7Yy2r3sylr8er9iFcCoHLeV2%2fc8%2bATxc%3d",
    ),
    2018: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZQftp%2fSyhp5PFMq2OvjCNY5I2g1KvY54tj%2fYiCsJFXfU3G5I4YhttIgztwDX6Njr6ZVU8vmK28oZ",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZQftp%2fSyhp5PFMq2OvjCNY6CJOHWnP0uhQfvR5RntaiW0kjQAjlpkFYK509VRn553jQrI8GTqJvhJGz51YMUHCM%3d",
    ),
    2020: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeVciCixC%2bz1ZNJL5NtW4BzqozO%2bIMmNKJ4X79NjbJKzdNUQuBLhZjPAWD9hH%2fRxos2PhS1bcx2H",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZeVciCixC%2bz1ZNJL5NtW4Bxejud%2fndV9iL%2bJYWPjg8O2BTMp%2bf8NHZ96RLwL01lpPfbLA4sutAwo1bgllFNkdd8%3d",
    ),
    2022: (
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZSMw8T%2bTP0Bu5MYScFngpt5KKJsa1cQ0Ak2xTeElyH2Rf2dTv5xVv%2bzcqC31P7B96%2ftQl5sPSN5M",
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZSMw8T%2bTP0Bu5MYScFngpt7DT1jIISrO8vbwEz2j1UaUFk4Ught6jyKPD0J6ZjQaef6%2fy5lLn0i%2brhqAS%2fOCDIRvrRtL96BN5g%3d%3d",
    ),
}
WA_DAYS = [held for held in wa_local.DAYS if held.month == 11 and held.year >= 2016]
NJ_FILES = {
    year: [f"{year}/{year}-official-general-results-{body}.pdf" for body in bodies]
    for year, bodies in (
        (2019, ["general-assembly"]),
        (2021, ["general-assembly", "state-senate"]),
        (2023, ["general-assembly", "state-senate"]),
        (2025, ["general-assembly"]),
    )
}
NJ_UNITS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
]
NJ_TENS = {"twentieth": 20, "thirtieth": 30, "fortieth": 40, "twenty": 20, "thirty": 30, "forty": 40}
FL_RACES = {"STS": "senate", "STR": "house"}
IL_OFFICE = re.compile(r"(\d+)(?:ST|ND|RD|TH) (REPRESENTATIVE|SENATE)")
IL_CITIES = {"cityofchicago": "cook"}
FL_DAYS = [date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in range(2000, 2023, 2)]


def day(year: int) -> date:
    return date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)


def fetch() -> None:
    for held in FL_DAYS:
        if held.year > 2014:
            raw.fetch(SOURCE, fl_sos.EXTRACT.format(day=held), suffix=".txt")
    for files in IL_FILES.values():
        for token in files:
            raw.fetch(SOURCE, IL_DOWNLOAD.format(token), suffix=".txt")
    for paths in NJ_FILES.values():
        for path in paths:
            raw.fetch(SOURCE, nj_sos.RESULTS + path, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for held in FL_DAYS:
        frame = pl.read_csv(
            io.BytesIO(
                raw.cached(fl_sos.SOURCE if held.year < 2015 else SOURCE, fl_sos.EXTRACT.format(day=held))
                .path.read_bytes()
                .decode("cp1252")
                .encode()
            ),
            separator="\t",
            infer_schema_length=0,
            quote_char=None,
        ).filter(pl.col("RaceCode").is_in(list(FL_RACES)))
        frames.append(
            frame.select(
                year=pl.lit(held.year),
                chamber=pl.concat_str(
                    pl.col("RaceCode").replace_strict(FL_RACES),
                    pl.col("Juris1num").cast(pl.Int16).cast(pl.String),
                    pl.lit("g"),
                    separator=":",
                ),
                state=pl.lit("FL"),
                code=pl.col("CountyName")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace({"dade": "miamidade"})
                .replace_strict(fips["FL"]),
                candidate=pl.concat_str(
                    *(
                        pl.col(column).fill_null("").str.replace_all(",", "").str.strip_chars()
                        for column in ("CanNameFirst", "CanNameMiddle", "CanNameLast")
                    ),
                    separator=" ",
                )
                .str.replace_all(r"\s+", " ")
                .str.strip_chars()
                .str.to_uppercase(),
                votes=pl.col("CanVotes").cast(pl.Int64),
            )
        )
    rows = []
    for year, (county, total) in IL_FILES.items():
        books = []
        for token in (county, total):
            text = raw.cached(SOURCE, IL_DOWNLOAD.format(token)).path.read_text(encoding="latin-1")
            lines = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text.split("\n", 1)[0] else ","))
            heads = [cell.strip() for cell in lines[0]]
            books.append(
                [dict(zip(heads, [cell.strip() for cell in line], strict=False)) for line in lines[1:] if any(line)]
            )
        sums: dict[tuple[str, str], int] = {}
        for row in books[0]:
            if (found := IL_OFFICE.fullmatch(row["OfficeName"])) is None:
                continue
            sums[(row["OfficeName"], row["CandidateID"])] = sums.get((row["OfficeName"], row["CandidateID"]), 0) + int(
                row["Votes"]
            )
            plain = re.sub(r"[^a-z]", "", row["County"].lower())
            rows.append(
                (
                    year,
                    f"{'house' if found[2] == 'REPRESENTATIVE' else 'senate'}:{int(found[1])}:g",
                    "IL",
                    fips["IL"][IL_CITIES.get(plain, plain)],
                    re.sub(r"\s+", " ", f"{row['CanFirstName']} {row['CanLastName']}").strip().upper(),
                    int(row["Votes"]),
                )
            )
        if sums != {
            (row["OfficeName"], row["CandidateID"]): int(row["Votes"])
            for row in books[1]
            if IL_OFFICE.fullmatch(row["OfficeName"])
        }:
            raise ValueError(f"{year} Illinois: the county rows do not match the statewide totals")
    frames.append(
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "state": pl.String,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
    )
    counties = sorted((county.upper() for county in nj_sos.COUNTIES), key=len, reverse=True)
    rows = []
    for year, paths in NJ_FILES.items():
        for path in paths:
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, nj_sos.RESULTS + path).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            district, name, votes = None, None, {}
            for line in text.splitlines():
                if found := re.match(r"\s*([A-Za-z-]+) Legislative District", line):
                    parts = found[1].lower().split("-")
                    district = (
                        NJ_TENS[parts[0]] + (NJ_UNITS.index(parts[1]) + 1 if len(parts) > 1 else 0)
                        if parts[0] in NJ_TENS
                        else NJ_UNITS.index(parts[0]) + 1
                    )
                    name = None
                elif found := re.match(r"\s+Total\s+([\d,]+)\s*$", line):
                    if name is not None and votes:
                        if sum(votes.values()) != int(found[1].replace(",", "")):
                            raise ValueError(f"{path}: {name}'s counties don't add up to the total")
                        rows.extend(
                            (
                                year,
                                f"{'senate' if 'senate' in path else 'general-assembly'}:{district}:g",
                                "NJ",
                                fips["NJ"][re.sub(r"[^a-z]", "", county.lower())],
                                name,
                                count,
                            )
                            for county, count in votes.items()
                        )
                    name, votes = None, {}
                elif re.match(r"\S", line) and district and not re.match(r"(Name|Counties|\d\d/\d\d/\d{4})\b", line):
                    name = (
                        name
                        or re.sub(
                            r"\s*(\(w\)|\*|\s\d+[A-Z]?\s|\sP\.?\s?O\.?\s).*$", "", re.split(r"\s{2,}", line)[0]
                        ).upper()
                    )
                elif len(line) - len(line.lstrip()) >= 50:
                    county = next((county for county in counties if line.strip().startswith(county + " ")), None)
                    if county and (found := re.search(r"([\d,]+)$", line.strip())):
                        votes[county] = votes.get(county, 0) + int(found[1].replace(",", ""))
    frames.append(
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "state": pl.String,
                "code": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
    )
    for held in WA_DAYS:
        race = pl.col("Race").str.strip_chars()
        found = race.str.extract_groups(r"(?i)^legislative district (\d+) state (senator|representative pos\. (\d))$")
        frames.append(
            pl.read_csv(raw.cached(wa_local.SOURCE, wa_local.FILES[held]).path, infer_schema_length=0)
            .with_columns(found=found)
            .filter(pl.col("found").struct.field("1").is_not_null())
            .select(
                year=pl.lit(held.year),
                chamber=pl.concat_str(
                    pl.when(pl.col("found").struct.field("2").str.to_lowercase() == "senator")
                    .then(pl.lit("senate"))
                    .otherwise(pl.lit("house")),
                    pl.col("found").struct.field("1").cast(pl.Int16).cast(pl.String),
                    pl.col("found").struct.field("3"),
                    pl.lit("gs" if held.year % 2 else "g"),
                    separator=":",
                    ignore_nulls=True,
                ),
                state=pl.lit("WA"),
                code=pl.col("County").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips["WA"]),
                candidate=pl.col("Candidate").str.strip_chars().str.to_uppercase(),
                votes=pl.col("Votes").cast(pl.Int64),
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.concat(frames, how="vertical_relaxed")
        .with_columns(
            day=pl.col("year").map_elements(day, return_dtype=pl.Date),
            special=pl.lit(False),
            district=pl.lit(None, pl.Int16),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source="state")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's, Illinois's, Washington's, and New Jersey's state legislative general elections by county, "
            "2000–2025, from the Division of Elections' results extracts, the State Board of Elections' vote totals, "
            "the Secretary of State's county results exports, and the Division of Elections' official results",
            polity="US",
        )
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   where r.status = 'active' and x.value ~ '^20..-11-..:(FL|IL):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))""",
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(covered))
        )
        sources = {
            "FL": cs.source(fl_sos.PUBLISHER, fl_sos.TITLE),
            "IL": cs.source(IL_PUBLISHER, IL_TITLE),
            "WA": cs.source(wa_local.PUBLISHER, wa_local.TITLE),
            "NJ": cs.source(nj_sos.PUBLISHER, nj_sos.TITLE),
        }
        for state, publisher, title, url in (
            ("FL", fl_sos.PUBLISHER, fl_sos.TITLE, "https://results.elections.myflorida.com/"),
            ("IL", IL_PUBLISHER, IL_TITLE, "https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx"),
            ("WA", wa_local.PUBLISHER, wa_local.TITLE, "https://results.vote.wa.gov/results/"),
            (
                "NJ",
                nj_sos.PUBLISHER,
                nj_sos.TITLE,
                "https://www.nj.gov/state/elections/election-information-results.shtml",
            ),
        ):
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        for held in FL_DAYS:
            cs.artifact(
                raw.cached(fl_sos.SOURCE if held.year < 2015 else SOURCE, fl_sos.EXTRACT.format(day=held)),
                sources["FL"],
            )
        for paths in NJ_FILES.values():
            for path in paths:
                cs.artifact(raw.cached(SOURCE, nj_sos.RESULTS + path), sources["NJ"])
        for held in WA_DAYS:
            cs.artifact(raw.cached(wa_local.SOURCE, wa_local.FILES[held]), sources["WA"])
        for files in IL_FILES.values():
            for token in files:
                cs.artifact(raw.cached(SOURCE, IL_DOWNLOAD.format(token)), sources["IL"])
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
