import html
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nv_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
BASE = "http://www.nvsos.gov/SOSelectionPages/results/"
FILES = [
    (date(2000, 9, 5), "2001", BASE + "2000Primary/ElectionSummary.aspx"),
    (date(2002, 9, 3), "2003", BASE + "2002Primary/ElectionSummary.aspx"),
    (date(2004, 9, 7), "2005", BASE + "2004Primary/ElectionSummary.aspx"),
    (date(2006, 8, 15), "2007", BASE + "2006StateWidePrimary/ElectionSummary.aspx"),
    (date(2008, 8, 12), "2009", BASE + "2008StateWidePrimary/ElectionSummary.aspx"),
    (date(2010, 6, 8), "2011", BASE + "2010STatewidePrimary/ElectionSummary.aspx"),
    (date(2012, 6, 12), "2013", BASE + "2012STatewidePrimary/ElectionSummary.aspx"),
    (date(2014, 6, 10), "2015", BASE + "2014STatewidePrimary/ElectionSummary.aspx"),
    (date(2016, 6, 14), "2017", BASE + "2016STatewidePrimary/ElectionSummary.aspx"),
]
COUNTIES = {
    "CARSON CITY": "32510",
    "CHURCHILL": "32001",
    "CLARK": "32003",
    "DOUGLAS": "32005",
    "ELKO": "32007",
    "ESMERALDA": "32009",
    "EUREKA": "32011",
    "HUMBOLDT": "32013",
    "LANDER": "32015",
    "LINCOLN": "32017",
    "LYON": "32019",
    "MINERAL": "32021",
    "NYE": "32023",
    "PERSHING": "32027",
    "STOREY": "32029",
    "WASHOE": "32031",
    "WHITE PINE": "32033",
}
PARTIES = {
    "DEMOCRATIC": "democratic",
    "DEM": "democratic",
    "REPUBLICAN": "republican",
    "REP": "republican",
    "LIBERTARIAN": "libertarian",
    "LIB": "libertarian",
}
PUBLISHER = "Nevada Secretary of State"
TITLE = "Election results: primary election summary by county"


def fetch() -> None:
    for _, stamp, url in FILES:
        target = WAYBACK.format(stamp, url)
        try:
            raw.cached(SOURCE, target)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfL", "--resolve", "web.archive.org:443:207.241.237.2", "-A", raw.USER_AGENT, target],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE, target, body, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html"
            )


def parse() -> None:
    rows = []
    for day, stamp, url in FILES:
        text = raw.cached(SOURCE, WAYBACK.format(stamp, url)).path.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.S)
        text = re.sub(r"(\|\s*)+", "|", html.unescape(re.sub(r"<[^>]+>", "|", text)))
        tokens = [token.strip() for token in text.split("|")]
        for at, token in enumerate(tokens):
            if token != "Percent" or tokens[at + 1] != "Total Votes":
                continue
            title = " ".join(tokens[at - 1].upper().split())
            party = re.search(r"\((\w[\w ]*)\)\s*$", title)
            district = re.search(r"DISTRICT\s+(\d+)", title)
            if "VICE" in title or party is None or party[1].upper() not in PARTIES:
                continue
            if re.match(r"(UNITED STATES SENATOR|U\.S\. SENATOR)", title):
                chamber, number = "senate", None
            elif re.match(r"(U\.S\. )?REPRESENTATIVE IN CONGRESS", title) and district:
                chamber, number = "house", int(district[1])
            elif re.match(r"PRESIDENT", title):
                chamber, number = "president", None
            else:
                continue
            names = []
            cursor = at + 2
            while tokens[cursor] in COUNTIES or tokens[cursor].title() in {c.title() for c in COUNTIES}:
                names.append(tokens[cursor].upper())
                cursor += 1
            width = len(names)
            while cursor + 2 + width < len(tokens) and re.fullmatch(r"[\d.]+%", tokens[cursor + 1]):
                candidate = tokens[cursor]
                for county, cell in zip(names, tokens[cursor + 3 : cursor + 3 + width], strict=True):
                    if cell != "N/A" and not re.match(r"(NONE OF|UNDER VOTES|OVER VOTES)", candidate.upper()):
                        rows.append(
                            (
                                day,
                                f"{chamber}:{PARTIES[party[1].upper()]}",
                                number,
                                COUNTIES[county],
                                candidate.upper(),
                                int(cell.replace(",", "")),
                            )
                        )
                cursor += 3 + width
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "chamber", "district", "code", "candidate", "votes"], orient="row").with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("NV"),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("NV"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nevada's U.S. Senate, House, and presidential primaries by county from the Secretary of State's election summary pages",
            polity="US-NV",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.nvsos.gov/sos/elections/election-information/election-results",
            license="Public record",
        )
        for _, stamp, url in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, url)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NV": source}, strict=True)
        return cs.write()
