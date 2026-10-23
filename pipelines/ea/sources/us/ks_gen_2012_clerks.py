import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_gen_2012_clerks"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kansas county election officers"
TITLE = "November 6, 2012 general election official results by county"
LANDING = "https://sos.ks.gov/elections/election-results.html"
DAY = date(2012, 11, 6)
SEDGWICK = (
    "https://web.archive.org/web/20191002193206id_/"
    "https://www.sedgwickcounty.org/elections/election-results/2012-general/"
)
SHAWNEE = "https://web.archive.org/web/20141117153655id_/http://www.snco.us:80/election/prev/20121106-general.htm"
OFFICE = re.compile(r"State (Senat(?:e|or)|Representative) (\d+)(?:st|nd|rd|th|ST|ND|RD|TH)(?: DISTRICT)?")


def fetch() -> None:
    for url in (SEDGWICK, SHAWNEE):
        raw.fetch(SOURCE, url, suffix=".html")


def lines(url: str) -> list[str]:
    text = raw.cached(SOURCE, url).path.read_text(errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    return [line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "\n", text)).splitlines() if line.strip()]


def parse() -> None:
    rows: list[tuple[str, str, str, int]] = []
    found = lines(SEDGWICK)
    if "Results are official." not in found:
        raise ValueError("Sedgwick's page is not marked official")
    chamber = None
    for index, line in enumerate(found):
        if office := OFFICE.fullmatch(line):
            chamber = f"{'senate' if office[1].startswith('Senat') else 'house'}:{int(office[2])}:g"
            continue
        if re.fullmatch(r"\d+ of \d+ precincts reported \(100%\)", line) or line.endswith("DISTRICT"):
            chamber = None
            continue
        if chamber and line not in ("Candidate", "Votes", "Percent") and re.fullmatch(r"[\d,]+", found[index + 1]):
            if not re.fullmatch(r"[\d.]+%", found[index + 2]):
                raise ValueError(f"Sedgwick {chamber} {line}: {found[index + 1 : index + 3]}")
            rows.append((chamber, "20173", line, int(found[index + 1].replace(",", ""))))
    found = lines(SHAWNEE)
    if "Final Official" not in found:
        raise ValueError("Shawnee's page is not marked official")
    chamber = None
    for index, line in enumerate(found):
        if office := OFFICE.fullmatch(line):
            chamber = f"{'senate' if office[1].startswith('Senat') else 'house'}:{int(office[2])}:g"
            continue
        if not re.fullmatch(r"\d+", line) and line != "Total" and not re.fullmatch(r"\d+", found[index + 1]):
            chamber = None
            continue
        if chamber and line != "Total" and re.fullmatch(r"\d+", found[index + 1]):
            rows.append((chamber, "20177", re.sub(r'"(\w+)"', r' "\1" ', line), int(found[index + 1])))
    frame = pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    print(frame.group_by("code").agg(pl.col("chamber").n_unique()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        candidate=pl.col("candidate").str.split(" ").list.eval(pl.element().filter(pl.element() != "")).list.join(" "),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("KS"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate") == "WRITE-IN",
        kind=pl.lit("county"),
        source=pl.lit("KS"),
    ).with_columns(candidate=pl.col("candidate").str.to_uppercase()).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's 2012 State Senate and State House generals in Sedgwick and Shawnee from the county "
            "election officers' official results",
            polity="US-KS",
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
        for url in (SEDGWICK, SHAWNEE):
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"KS": source}, strict=True)
        return cs.write()
