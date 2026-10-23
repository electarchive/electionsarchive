import html
import json
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_pri_2014_clerks"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Kansas county election officers"
TITLE = "August 5, 2014 primary election official results by county"
LANDING = "https://sos.ks.gov/elections/election-results.html"
DAY = date(2014, 8, 5)
JOHNSON = (
    "https://www.jocoelection.org/sites/default/files/2022-11/"
    "August%20Primary%20Election%20-%20August%205%2C%202014%20Precinct%20Report.pdf"
)
MIAMI = "https://results.enr.clarityelections.com/KS/Miami/50885/138347/json/sum.json"
SEDGWICK = "https://www.sedgwickcounty.org/elections/election-results/2014-primary/"
BUTLER = "https://web.archive.org/web/20150415033404id_/http://maps.bucoks.com/elections/public/G06.php?election=24"
FILES = {JOHNSON: ".pdf", MIAMI: ".json", SEDGWICK: ".html", BUTLER: ".html"}


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)


def lines(url: str) -> list[str]:
    text = raw.cached(SOURCE, url).path.read_text(errors="replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
    return [line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "\n", text)).splitlines() if line.strip()]


def parse() -> None:
    rows: list[tuple[str, str, str, int]] = []
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, JOHNSON).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    page = next(page for page in text.split("\f") if "STATE SEN 37 R" in page and re.search(r"(?m)^\s*Total\b", page))
    total = next(line for line in page.splitlines() if re.match(r"\s*Total\b", line)).split()
    rows += [
        ("senate:37:rp", "20091", "Molly Baumgardner", int(total[5])),
        ("senate:37:rp", "20091", "Charlotte O'Hara", int(total[7])),
    ]
    miami = json.loads(raw.cached(SOURCE, MIAMI).path.read_text())
    contest = next(contest for contest in miami["Contests"] if contest["C"] == "State Senate 37th Dist (REP)")
    if contest["PR"] != contest["TP"]:
        raise ValueError(f"Miami reports {contest['PR']} of {contest['TP']} precincts")
    rows += [
        ("senate:37:rp", "20121", name, int(votes)) for name, votes in zip(contest["CH"], contest["V"], strict=True)
    ]
    found = lines(SEDGWICK)
    start = found.index("State Representative 99TH DISTRICT (Republican Primary)")
    if "Results are official." not in found:
        raise ValueError("Sedgwick's page is not marked official")
    for name in ("Randy J. Banwart", "Dennis E. Hedke"):
        rows.append(("house:99:rp", "20173", name, int(found[found.index(name, start) + 1].replace(",", ""))))
    found = lines(BUTLER)
    start = len(found) - 1 - found[::-1].index("State Representative DISTRICT 99")
    if found[start + 1] != "5 of 5 precincts reporting.":
        raise ValueError(f"Butler reports {found[start + 1]}")
    for index in range(start, start + 12):
        if candidate := re.fullmatch(r"\*?\s*(.+?)\s+\(REP\)", found[index]):
            rows.append(("house:99:rp", "20015", candidate[1], int(found[index + 2].rstrip(" *").replace(",", ""))))
    frame = pl.DataFrame(
        rows, schema={"chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    )
    print(frame)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        candidate=pl.col("candidate").str.to_uppercase(),
        year=pl.lit(DAY.year, pl.Int32),
        day=pl.lit(DAY),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("KS"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("KS"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's 2014 State Senate 37 and State House 99 Republican primaries by county from the county "
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
        for url in FILES:
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"KS": source}, strict=True)
        return cs.write()
