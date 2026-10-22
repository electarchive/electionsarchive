import csv
import difflib
import io
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, state_primaries
from ea.store import DATA, connect

SOURCE = "us.il_vote_totals"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
CACHE = state_primaries.SOURCE
FEDERAL = re.compile(r"(?:(\d+)(?:ST|ND|RD|TH) CONGRESS|(UNITED STATES SENATOR)|(PRESIDENT))")
GENERALS = {
    1998: (
        date(1998, 11, 3),
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZbxtiJ%2fosJgrbL31aBh9d%2f%2bNJIzsDGBT%2bfoloSjs%2f6ALb494jITMllVoS7%2btZRrxqEsxCBlttI4l9csZTGqYG0g%3d",
    )
}
SCANS = {
    1990: (
        date(1990, 11, 6),
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZXrHCzqRzWcetZpt%2bDb3HXpXLIL9t24cXpacS2tFsBti%2bDPgAStQjUCCpr0fVwKSZdn0bkGpN0jf",
    ),
    1992: (
        date(1992, 11, 3),
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZXrHCzqRzWcetZpt%2bDb3HXpXLIL9t24cXtTS4sHqtrLrmkQMebVS3mfhc%2fKYW43XFJIhitTAKjyz",
    ),
    1994: (
        date(1994, 11, 8),
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZXrHCzqRzWcetZpt%2bDb3HXpXLIL9t24cXsieEFM9Snz0TK%2fAE2%2fDZslfe6eoBdwEVu117Zl4GM2N",
    ),
    1996: (
        date(1996, 11, 5),
        "khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZXrHCzqRzWcetZpt%2bDb3HXpXLIL9t24cXjTuupDlnxm8vcckbcQT8KZzCLsbqlLlvRI%2fz3q5vo%2bd",
    ),
}
ORDINALS = [
    "FIRST",
    "SECOND",
    "THIRD",
    "FOURTH",
    "FIFTH",
    "SIXTH",
    "SEVENTH",
    "EIGHTH",
    "NINTH",
    "TENTH",
    "ELEVENTH",
    "TWELFTH",
    "THIRTEENTH",
    "FOURTEENTH",
    "FIFTEENTH",
    "SIXTEENTH",
    "SEVENTEENTH",
    "EIGHTEENTH",
    "NINETEENTH",
    "TWENTIETH",
    "TWENTY-FIRST",
    "TWENTY-SECOND",
]
DIGITS = str.maketrans("!IlOB", "11108")
FILES = [
    *(
        (state_primaries.IL_DAYS[year], state_primaries.IL_FILES[year][0], False)
        for year in state_primaries.IL_FILES
        if year <= 2014
    ),
    *((day, token, True) for day, token in GENERALS.values()),
    *((day, token, True) for day, token in SCANS.values()),
]
PUBLISHER = "Illinois State Board of Elections"
TITLE = "Election vote totals"
PARTIES = {"DEM": "democratic", "REP": "republican"}


def fetch() -> None:
    for _, token in GENERALS.values():
        raw.fetch(CACHE, state_primaries.IL_DOWNLOAD.format(token), suffix=".txt")
    for _, token in SCANS.values():
        raw.fetch(CACHE, state_primaries.IL_DOWNLOAD.format(token), suffix=".pdf")


def scan_rows(path: Path, counties: dict[str, str]) -> list[tuple[int, str, str, int]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    text = text.replace("•", ".").replace("·", ".")
    text = re.sub(r"(\d)\s*[',`]\s*([\dIO!lB]{3})\b", r"\1\2", text)
    lines = text.split(chr(10))
    out: list[tuple[int, str, str, int]] = []
    index = 0
    while index < len(lines):
        found = re.match(r"\s*([A-Z\-]+) CONGRESSIONAL DISTRICT", lines[index])
        if lines[index - 1].strip() != "REPRESENTATIVE IN CONGRESS" or found is None or found[1] not in ORDINALS:
            index += 1
            continue
        district = ORDINALS.index(found[1]) + 1
        names: list[str] = []
        totals: list[int] = []
        rows: list[tuple[str, list[int]]] = []
        counting = False
        index += 1
        while index < len(lines) and not re.match(
            r"\s*(REPRESENTATIVE IN|STATE SENATOR|UNITED STATES|JUDICIAL)", lines[index]
        ):
            line = lines[index]
            index += 1
            if re.search(r"\bCOUNTY\b", line):
                counting = True
            elif not counting:
                cut = re.match(r"\s*(?:\(WON\)\s*)?(.*?)\.{3,}(.*)$", line)
                if cut is not None and "PLURAL" not in line and re.search(r"\d", cut[2]):
                    rest = cut[2].split()
                    digits = re.sub(r"\D", "", next(token for token in rest if token[0].isdigit()).translate(DIGITS))
                    names.append(re.sub(r"\s+", " ", cut[1]).strip().upper())
                    totals.append(int(digits))
            elif line.strip() and not re.match(r"\s*(-+|- \d+ -)", line):
                tokens = line.split()
                values: list[str] = []
                while tokens and re.fullmatch(r"[\d,.'`!IlOB]+", tokens[-1]) and re.search(r"\d", tokens[-1]):
                    values.append(tokens.pop())
                values.reverse()
                if names and len(values) >= len(names):
                    label = re.sub(
                        r"[^a-z]",
                        "",
                        re.sub(
                            r"(DEM|REP|OEM)$", "", "".join(token for token in tokens if re.search("[A-Za-z]", token))
                        ).lower(),
                    )
                    rows.append(
                        (
                            label,
                            [int(re.sub(r"\D", "", value.translate(DIGITS)) or 0) for value in values[-len(names) :]],
                        )
                    )
        if not names or any(sum(row[1][slot] for row in rows) != total for slot, total in enumerate(totals)):
            print(f"{path.name}: district {district} fails its column check, skipped")
            continue
        placed: list[str] = []
        found_rows: list[tuple[int, str, str, int]] = []
        for label, votes in rows:
            match = difflib.get_close_matches(label, list(counties), n=1, cutoff=0.6)
            if not match:
                break
            placed.append(counties[match[0]])
            found_rows.extend(
                (district, name, counties[match[0]], count) for name, count in zip(names, votes, strict=True)
            )
        else:
            if len(set(placed)) != len(placed):
                print(f"{path.name}: district {district} repeats a county, skipped")
                continue
            out.extend(found_rows)
            continue
        print(f"{path.name}: district {district} has an unreadable county name, skipped")
    return out


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
    rows: list[tuple[object, str, int | None, str, str, int]] = []
    for day, token, general in FILES:
        artifact = raw.cached(CACHE, state_primaries.IL_DOWNLOAD.format(token))
        if day.year < 1998:
            rows.extend(
                (day, "house", district, name, code, votes)
                for district, name, code, votes in scan_rows(artifact.path, fips)
            )
            continue
        text = artifact.path.read_text(encoding="latin-1")
        table = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text.split("\n", 1)[0] else ","))
        heads = [cell.strip() for cell in table[0]]
        for row in table[1:]:
            if not any(row):
                continue
            line = dict(zip(heads, [cell.strip() for cell in row], strict=False))
            found = FEDERAL.fullmatch(line["OfficeName"])
            party = line["PartyAbbrev"] if general else PARTIES.get(line["PartyAbbrev"])
            if found is None or party is None:
                continue
            kind = "house" if found[1] else "senate" if found[2] else "president"
            name = re.sub(r"\s+", " ", f"{line['CanFirstName']} {line['CanLastName']}").strip().upper()
            rows.append(
                (
                    day,
                    kind if general else f"{kind}:{party}",
                    int(found[1]) if found[1] else None,
                    name,
                    fips[re.sub(r"[^a-z]", "", line["County"].lower())],
                    int(line["Votes"] or 0),
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "candidate", "code")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("IL"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
            kind=pl.lit("county"),
            source=pl.lit("IL"),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's 2000–2014 primaries and 1998 general House and Senate results by county",
            polity="US-IL",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
            license="Public record",
        )
        for _, token, _ in FILES:
            cs.artifact(raw.cached(CACHE, state_primaries.IL_DOWNLOAD.format(token)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IL": source}, strict=True)
        return cs.write()
