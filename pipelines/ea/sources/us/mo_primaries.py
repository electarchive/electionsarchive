import csv
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fec_totals, medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.mo_primaries"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/2025id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
LIVE = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
FILES = {
    date(1998, 8, 4): [SITE + "CountyPrimary1998.pdf"],
    date(2000, 8, 8): [SITE + "CountyPrimary2000.pdf"],
    date(2004, 8, 3): [SITE + "CountyPrimary2004.pdf", SITE + "CountyPrimary2004Part2.pdf"],
    date(2006, 8, 8): [LIVE + "CountyPrimary2006.pdf", LIVE + "CountyPrimary2006Part2.pdf"],
    date(2008, 2, 5): [LIVE + "CountyPresidential2008.pdf", LIVE + "CountyPresidential2008Part2.pdf"],
    date(2008, 8, 5): [LIVE + "CountyPrimary2008.pdf", LIVE + "CountyPrimary2008Part2.pdf"],
    date(2012, 2, 7): [SITE + "CountyPresidential2012.pdf", SITE + "CountyPresidential2012Part2.pdf"],
}
PARTIAL = {date(2000, 8, 8): 5}
STATS = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
MATRICES = {
    date(2016, 3, 15): "ActualResults-April122016.pdf",
    date(2016, 8, 2): "ActualResults-August22016.pdf",
    date(2018, 8, 7): "ActualResults-August72018.pdf",
    date(2020, 3, 10): "ACTUALRESULTS-March102020.pdf",
    date(2020, 8, 4): "ActualResults-August42020.pdf",
    date(2022, 8, 2): "ActualResults-August22022.pdf",
}
SHEETS = {
    date(2012, 8, 7): "2012/20120807__mo__primary.csv",
    date(2014, 8, 5): "2014/20140805__mo__primary.csv",
}
PARTIES = {
    "DEM": "democratic",
    "REP": "republican",
    "LIB": "libertarian",
    "CST": "constitution",
    "CON": "constitution",
    "REF": "reform",
    "UST": "u-s-taxpayers",
    "NLP": "natural-law",
    "GRE": "green",
    "GRN": "green",
}
PUBLISHER = "Missouri Secretary of State"
TITLE = "County reporting: official primary election returns"
OE_TITLE = "Missouri primary results by county, 2012 and 2014"


def fetch() -> None:
    for urls in FILES.values():
        for url in urls:
            if url.startswith(LIVE):
                try:
                    raw.cached(SOURCE, url)
                    continue
                except LookupError:
                    pass
                response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0 Safari/537.36"})
                response.raise_for_status()
                raw.store(SOURCE, url, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")
            else:
                raw.fetch(SOURCE, url, suffix=".pdf")
    for name in MATRICES.values():
        raw.fetch(SOURCE, STATS + name, suffix=".pdf")
    for path in SHEETS.values():
        raw.fetch(
            SOURCE,
            openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path),
            suffix=".csv",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%'"""
            ).fetchall()
        }
    codes |= {"stlouis": "29189", "stlouiscity": "29510", "stlouiscounty": "29189", "kansascity": "2938000"}
    rows: list[dict[str, object]] = []
    for day, urls in FILES.items():
        for url in urls:
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            county = None
            office: tuple[str, int | None] | None = None
            lines: list[dict[str, object]] = []
            for line in text.splitlines():
                if re.match(r"(http|\d+/\d+/\d{4}\s|\* Election)", line):
                    continue
                if heading := re.fullmatch(r" {0,5}([A-Z][A-Z .']+)", line):
                    county, office = codes[re.sub(r"[^a-z]", "", heading[1].lower())], None
                elif re.fullmatch(r" {0,5}U\.S\. Senator(?:\s{2,}Precincts.*)?", line):
                    office, lines = ("senate", None), []
                elif found := re.fullmatch(r" {0,5}U\.S\. Representative \W+ District (\d+)(?:\s{2,}Precincts.*)?", line):
                    office, lines = ("house", int(found[1])), []
                elif re.fullmatch(r" {0,5}U\.S\. President And Vice President(?:\s{2,}Precincts.*)?", line):
                    office, lines = ("president", None), []
                elif re.fullmatch(r" {0,5}\S.*", line):
                    office = None
                elif office is not None and (
                    row := re.fullmatch(r"\s+(\S.*?)\s{2,}([A-Z]{2,3})\s+([\d,]+)(?:\s+[\d.]+%)?(?:\d+/\d+)?\s*", line)
                ):
                    last, _, first = row[1].partition(", ")
                    lines.append(
                        {
                            "day": day,
                            "chamber": f"{office[0]}:{PARTIES[row[2]]}",
                            "district": office[1],
                            "code": county,
                            "candidate": f"{first} {last}".strip().upper(),
                            "writein": False,
                            "votes": int(row[3].replace(",", "")),
                            "source": "MO",
                        }
                    )
                elif office is not None and (total := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)):
                    if sum(int(r["votes"]) for r in lines) != int(total[1].replace(",", "")):
                        raise ValueError(f"{day} {county} {office}: rows do not sum to {total[1]}")
                    rows.extend(lines)
                    office = None
    party_label = re.compile(r"Republican|Democrat(?:ic)?|Libertarian|Green|Constitution")
    entry = re.compile(r"(\S.*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*")
    known = connect()
    for day, name in MATRICES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, STATS + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        tables: list[dict[str, object]] = []
        head: list[str] = []
        table: dict[str, object] | None = None
        for line in text.splitlines():
            if not line.strip() or re.search(
                r"Missouri Office of Secretary|OFFICIAL RESULTS|(Primary|General) Election -|Preference Primary -", line
            ):
                continue
            if (found := entry.fullmatch(line.strip())) and head:
                if table is None:
                    title = " ".join(head)
                    previous = tables[-1] if tables else None
                    if previous is not None and previous["title"] == title and not previous["closed"]:
                        table = previous
                    else:
                        office: tuple[str, int | None] | None = None
                        if re.search(r"State (Senator|Representative)", title):
                            office = None
                        elif re.search(r"President", title):
                            office = ("president", None)
                        elif re.search(r"(United States|U\.? ?S\.?) Senator", title):
                            office = ("senate", None)
                        elif (seat := re.search(r"Representative.*?District (\d+)", title)) and re.search(
                            r"U\.? ?S\.? Representative|United States Representative|Representative in Congress", title
                        ):
                            office = ("house", int(seat[1]))
                        table = {
                            "office": office,
                            "title": title,
                            "labels": party_label.findall(head[0]),
                            "rows": {},
                            "closed": False,
                        }
                        tables.append(table)
                rows_here = table["rows"]
                assert isinstance(rows_here, dict)
                rows_here[found[1]] = [int(value.replace(",", "")) for value in found[2].split()]
                table["closed"] = found[1].upper() == "TOTAL"
                continue
            if table is not None:
                head, table = [], None
            head.append(re.sub(r"\s+", " ", line.strip()))
        grouped: dict[tuple[str, int | None], list[dict[str, object]]] = {}
        for table in tables:
            if table["office"] is not None:
                grouped.setdefault(table["office"], []).append(table)
        for (kind, district), groups in grouped.items():
            columns: list[tuple[str, dict[str, int], int]] = []
            for group in groups:
                body = {county: values for county, values in group["rows"].items()}
                total_row = next(body.pop(county) for county in list(body) if county.upper() == "TOTAL")
                for at, label in enumerate(group["labels"]):
                    votes = {county: values[at] for county, values in body.items()}
                    if sum(votes.values()) != total_row[at]:
                        raise ValueError(f"{day} {kind} {district}: column {at} of {group['labels']} does not add up")
                    columns.append((label, votes, total_row[at]))
            for label, votes, total in columns:
                party = PARTIES[label.upper()[:3]]
                key = f"{day}:MO:" + (f"house:{district}" if district is not None else kind) + f":{party}"
                names = fec_totals.labels(known, key).get(total, [])
                if len(names) != 1:
                    print(f"{key}: {len(names)} candidates with {total}, skipped")
                    continue
                rows.extend(
                    {
                        "day": day,
                        "chamber": f"{kind}:{party}",
                        "district": district,
                        "code": codes[re.sub(r"[^a-z]", "", county.lower())],
                        "candidate": names[0].upper(),
                        "writein": False,
                        "votes": value,
                        "source": "MO",
                    }
                    for county, value in votes.items()
                )
    known.close()
    for day, path in SHEETS.items():
        url = openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path)
        with raw.cached(SOURCE, url).path.open(newline="") as handle:
            for entry in csv.DictReader(handle):
                if entry["office"] not in ("U.S. House", "U.S. Senate"):
                    continue
                rows.append(
                    {
                        "day": day,
                        "chamber": f"{'house' if entry['office'] == 'U.S. House' else 'senate'}:{PARTIES[entry['party']]}",
                        "district": int(entry["district"]) if entry["district"] else None,
                        "code": codes[re.sub(r"[^a-z]", "", entry["county"].lower().replace("saint ", "st "))],
                        "candidate": entry["candidate"].strip().upper(),
                        "writein": False,
                        "votes": int(entry["votes"].replace(",", "") or 0),
                        "source": "OE",
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows)
        .group_by("day", "chamber", "district", "code", "candidate", "source")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year(),
            special=pl.lit(False),
            state=pl.lit("MO"),
            district=pl.col("district").cast(pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
        )
        .sort("day", "chamber", "district", "code", "candidate")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's 1998, 2000, 2004, 2012, 2014, and 2016–2022 House, Senate, and presidential primaries by county",
            polity="US-MO",
        )
        sources = {"MO": cs.source(PUBLISHER, TITLE), "OE": cs.source("OpenElections", OE_TITLE)}
        cs.add(
            "source",
            id=sources["MO"],
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.mo.gov/elections/results",
            license="Public record",
        )
        cs.add(
            "source",
            id=sources["OE"],
            kind="community",
            publisher="OpenElections",
            title=OE_TITLE,
            url="https://github.com/openelections/openelections-data-mo",
            license="No license stated: facts published with attribution, raw files not redistributed",
        )
        for urls in FILES.values():
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources["MO"])
        for path in SHEETS.values():
            url = openelections.REPOSITORY.format(state="mo", commit=openelections.COMMITS["MO"], path=path)
            cs.artifact(raw.cached(SOURCE, url), sources["OE"])
        held = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^(1998|20(00|04|12|14))-08-[0-9]{2}:MO:(house:[0-9]+|senate):[a-z-]+$'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id and rs.status = 'active'
                                   and s.publisher <> 'Federal Election Commission' and s.id <> all(%s))""",
                [list(sources.values())],
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                pl.all_horizontal(
                    (pl.col("day") != day) | (pl.col("district") == district) for day, district in PARTIAL.items()
                ),
                ~pl.concat_str(
                    pl.col("day").cast(pl.String),
                    pl.lit("MO"),
                    pl.when(pl.col("district").is_not_null())
                    .then(pl.concat_str(pl.lit("house:"), pl.col("district").cast(pl.String)))
                    .otherwise(pl.lit("senate")),
                    pl.col("chamber").str.split(":").list.last(),
                    separator=":",
                ).is_in(list(held))
            ),
            sources,
            strict=True,
        )
        return cs.write()
