import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.wv_hist import PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.wv_leg_hist"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Historical election results: State Senate and House of Delegates"
SITE = "https://web.archive.org/web/{}id_/https://sos.wv.gov/elections/Documents/HistElecDocs/"
STAMPS = {(2002, "house-of-delegates"): "20210423101346"}
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7)}
FILES = {"house-of-delegates": "House%20of%20Delegates%20Gen", "senate": "State%20Senate%20Gen"}
PARTIES = {"Democrat", "Republican", "Libertarian", "Mountain", "Constitution", "Independent", "Green", "Unaffiliated"}
NUMBERS = re.compile(r"\d[\d,]*")


def url(year: int, office: str) -> str:
    return f"{SITE.format(STAMPS.get((year, office), 2019))}{year}/{year}%20{FILES[office]}.pdf"


def fetch() -> None:
    for year in DAYS:
        for office in FILES:
            raw.fetch(SOURCE, url(year, office), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
        seats: dict[date, dict[tuple[str, str], set[str]]] = {}
        for value, label in conn.execute(
            """select x.value, o.ballot_label from external_id x
               left join contest_option o on o.contest_id = x.entity_id and o.kind = 'candidate'
               where x.scheme = 'us.contest' and x.value ~ '^200[0246]-11-..:WV:senate:[0-9]+:[0-9]:g$'"""
        ).fetchall():
            held, _, _, district, seat, _ = value.split(":")
            seats.setdefault(date.fromisoformat(held), {}).setdefault((district, seat), set()).update(
                [re.sub(r",? (Jr|Sr|II|III)\.?$", "", label).split()[-1].upper()] if label else []
            )
    rows = []
    for year, day in DAYS.items():
        for office in FILES:
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year, office)).path), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
            district = None
            header: list[str] = []
            parts: list[tuple[str, str]] = []
            for line in text.splitlines():
                if found := re.fullmatch(r"\s*District (\d+)\s*", line):
                    district, header, parts = int(found[1]), [], []
                elif district is None:
                    continue
                elif line.startswith("County"):
                    header, parts = [" " * 6 + line[6:]], []
                elif header and not parts and not re.match(r"\S.*?\s{2,}\d", line):
                    header.append(line)
                elif header and (found := re.match(r"(\S.*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$", line)):
                    if found[1] != "Totals":
                        parts.append((found[1], line))
                        continue
                    centers = [m.start() + len(m[0]) / 2 for m in NUMBERS.finditer(line, found.start(2))]
                    names: list[list[str]] = [[] for _ in centers]
                    closed = [False] * len(centers)
                    for text_line in header:
                        for chunk in re.finditer(r"\S+(?: \S+)*", text_line):
                            column = min(
                                range(len(centers)), key=lambda k: abs(centers[k] - chunk.start() - len(chunk[0]) / 2)
                            )
                            if chunk[0] in PARTIES or chunk[0] == "Elected":
                                closed[column] = True
                            elif not closed[column]:
                                names[column].append(chunk[0])
                    totals = [int(n.replace(",", "")) for n in NUMBERS.findall(line, found.start(2))]
                    counted = [
                        (county, [int(n.replace(",", "")) for n in NUMBERS.findall(row, len(county))])
                        for county, row in parts
                    ]
                    sums = [
                        sum(votes[k] for _, votes in counted if len(votes) == len(totals)) for k in range(len(totals))
                    ]
                    if office == "senate":
                        found_seats = {
                            key: surnames for key, surnames in seats.get(day, {}).items() if key[0] == str(district)
                        } or seats.get(day, {})
                        if len(found_seats) > 1:
                            found_seats = {
                                key: surnames
                                for key, surnames in found_seats.items()
                                if any(
                                    re.sub(r",? (Jr|Sr|II|III)\.?$", "", " ".join(name)).split()[-1].upper() in surnames
                                    for name in names
                                    if name
                                )
                            }
                        chamber = ":".join(["senate", *next(iter(found_seats)), "g"]) if len(found_seats) == 1 else None
                    else:
                        chamber = f"house-of-delegates:{district}:g"
                    if (
                        chamber is None
                        or sums != totals
                        or any(len(v) != len(totals) for _, v in counted)
                        or not all(names)
                    ):
                        print(
                            f"{year} {office} {district}: {[' '.join(n) for n in names]} sum {sums} printed {totals}, skipped"
                        )
                    else:
                        rows.extend(
                            (
                                day,
                                chamber,
                                counties[re.sub(r"[^a-z]", "", county.lower())],
                                " ".join(names[k]),
                                votes[k],
                            )
                            for county, votes in counted
                            for k in range(len(totals))
                        )
                    header, parts = [], []
    PARSED.mkdir(parents=True, exist_ok=True)
    parsed = pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    )
    print(
        parsed.group_by("day", pl.col("chamber").str.split(":").list.first().alias("office"))
        .agg(pl.col("chamber").n_unique())
        .sort("day")
    )
    parsed.filter(~pl.col("candidate").str.starts_with("No candidate")).group_by(
        "day", "chamber", "code", "candidate"
    ).agg(pl.col("votes").sum()).with_columns(
        writein=pl.col("candidate").str.ends_with("Write-In"),
        candidate=pl.col("candidate")
        .str.replace(r"\s*Write-In$", "")
        .str.to_uppercase()
        .str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("WV"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WV"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's State Senate and House of Delegates generals by county, 2000-2006 (Secretary of State, via the Wayback Machine)",
            polity="US-WV",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://sos.wv.gov/elections/Pages/ElectionResults.aspx",
                license="Public record",
            )
        for year in DAYS:
            for office in FILES:
                cs.artifact(raw.cached(SOURCE, url(year, office)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WV": source}, strict=True)
        return cs.write()
