import csv
import io
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fl_sos, medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.leg_canvass"
VERSION = "1"
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
}
FL_RACES = {"STS": "senate", "STR": "house"}
IL_OFFICE = re.compile(r"(\d+)(?:ST|ND|RD|TH) (REPRESENTATIVE|SENATE)")
IL_CITIES = {"cityofchicago": "cook"}


def day(year: int) -> date:
    return date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)


def fetch() -> None:
    for files in IL_FILES.values():
        for token in files:
            raw.fetch(SOURCE, IL_DOWNLOAD.format(token), suffix=".txt")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for held in fl_sos.DAYS:
        if held.year < 2000:
            continue
        frame = pl.read_csv(
            io.BytesIO(
                raw.cached(fl_sos.SOURCE, fl_sos.EXTRACT.format(day=held)).path.read_bytes().decode("cp1252").encode()
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
            summary="Florida's and Illinois's state legislative general elections by county, 2000–2014, from the "
            "Division of Elections' results extracts and the State Board of Elections' vote totals",
            polity="US",
        )
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   join changeset c on c.id = r.changeset_id
                   where r.status = 'active' and c.pipeline <> %s and x.value ~ '^20..-11-..:(FL|IL):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))""",
                [SOURCE],
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(covered))
        )
        sources = {
            "FL": cs.source(fl_sos.PUBLISHER, fl_sos.TITLE),
            "IL": cs.source(IL_PUBLISHER, IL_TITLE),
        }
        for state, publisher, title, url in (
            ("FL", fl_sos.PUBLISHER, fl_sos.TITLE, "https://results.elections.myflorida.com/"),
            ("IL", IL_PUBLISHER, IL_TITLE, "https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx"),
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
        for held in fl_sos.DAYS:
            if held.year >= 2000:
                cs.artifact(raw.cached(fl_sos.SOURCE, fl_sos.EXTRACT.format(day=held)), sources["FL"])
        for files in IL_FILES.values():
            for token in files:
                cs.artifact(raw.cached(SOURCE, IL_DOWNLOAD.format(token)), sources["IL"])
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
