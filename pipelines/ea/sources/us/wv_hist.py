import re
import subprocess
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.wv_hist"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/20190915id_/https://sos.wv.gov/elections/Documents/HistElecDocs/"
PUBLISHER = "West Virginia Secretary of State"
TITLE = "Historical election results"
OFFICES = {
    "sos": "Secretary of State",
    "auditor": "Auditor",
    "treasurer": "Treasurer",
    "agri": "Commissioner of Agriculture",
    "ag": "Attorney General",
}
BOOKS = {
    1992: (date(1992, 11, 3), "1992/1992%20General%20Election%20-%20Official%20Election%20Returns.pdf"),
    1996: (date(1996, 11, 5), "1996/1996%20General%20Election%20-%20Official%20Election%20Returns.pdf"),
}
SHEETS = {
    2000: (
        date(2000, 11, 7),
        {
            "sos": "SoS",
            "auditor": "Auditor",
            "treasurer": "Treasurer",
            "agri": "Com%20of%20Ag",
            "ag": "Attorney%20General",
        },
    ),
    2004: (
        date(2004, 11, 2),
        {
            "sos": "SoS",
            "auditor": "Auditor",
            "treasurer": "State%20Treasurer",
            "agri": "Com%20of%20Ag",
            "ag": "Attorney%20General",
        },
    ),
}
HEADINGS = {
    "SECRETARY OF STATE": "sos",
    "AUDITOR": "auditor",
    "TREASURER": "treasurer",
    "STATE TREASURER": "treasurer",
    "COMMISSIONER OF AGRICULTURE": "agri",
    "ATTORNEY GENERAL": "ag",
}
KNOWN: dict[tuple[int, str, str], int] = {
    (1992, "sos", "Ken Hechler"): 20,
    (1992, "agri", "Steven C. Teufel"): 90,
    (2004, "ag", "Hiram Lewis, IV"): 72,
}
PARTIES = {
    "D": "DEMOCRATIC",
    "R": "REPUBLICAN",
    "L": "LIBERTARIAN",
    "C": "CONSTITUTION",
    "M": "MOUNTAIN",
    "I": "INDEPENDENT",
    "Democrat": "DEMOCRATIC",
    "Republican": "REPUBLICAN",
    "Libertarian": "LIBERTARIAN",
    "Constitution": "CONSTITUTION",
    "Mountain": "MOUNTAIN",
    "Independent": "INDEPENDENT",
}
METHOD = (
    "County totals come from the Secretary of State's official returns for the general election, each candidate's "
    "55 counties summing to the TOTAL row printed in the same document, except three columns whose county rows "
    "print more than the TOTAL row (Ken Hechler's 1992 secretary of state race by 20, Steven C. Teufel's 1992 "
    "commissioner of agriculture race by 90, and Hiram Lewis IV's 2004 attorney general race by 72); those county "
    "figures are kept as printed, and the TOTAL rows agree with the documents' printed total votes."
)
ROW = re.compile(r"^([A-Za-z.' ]+?)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")


def addresses() -> list[str]:
    urls = [SITE + path for _, path in BOOKS.values()]
    for year, (_, names) in SHEETS.items():
        urls += [f"{SITE}{year}/{year}%20{name}%20Gen.pdf" for name in names.values()]
    return urls


def fetch() -> None:
    for url in addresses():
        for attempt in range(4):
            try:
                raw.fetch(SOURCE, url, suffix=".pdf")
                break
            except Exception:
                time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(url)
        time.sleep(3)


def text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def table(lines: list[str], names: list[tuple[str, str]], office: str, year: int, where: str) -> list[tuple[int, str, str, str, str, int]]:
    counties: dict[str, list[int]] = {}
    totals: list[int] | None = None
    for line in lines:
        match = ROW.match(line)
        if match is None:
            continue
        values = [int(v.replace(",", "")) for v in match[2].split()]
        label = match[1].strip()
        if label.lower().startswith("total"):
            totals = values
            break
        if label.lower().startswith("%"):
            continue
        counties[label] = values
    if totals is None or len(counties) != 55 or len(totals) != len(names):
        raise ValueError(f"{where}: {len(counties)} counties, totals {totals}, {len(names)} candidates")
    rows = []
    for index, (name, party) in enumerate(names):
        summed = sum(values[index] for values in counties.values())
        if summed != totals[index]:
            print(f"{where} {name}: counties sum to {summed}, printed total {totals[index]}")
            if KNOWN.get((year, office, name)) != summed - totals[index]:
                raise ValueError(f"{where} {name}: counties sum to {summed}, printed total {totals[index]}")
        rows += [(year, office, name, party, county, values[index]) for county, values in counties.items()]
    return rows


def parse() -> None:
    rows: list[tuple[int, str, str, str, str, int]] = []
    for year, (_, path) in BOOKS.items():
        lines = text(SITE + path).splitlines()
        starts = []
        for index, line in enumerate(lines):
            heading = HEADINGS.get(line.strip().upper())
            if heading is None:
                continue
            following = next((x for x in lines[index + 1 : index + 6] if x.strip()), "")
            if following.strip().startswith("*Elected") or re.search(r"\([A-Z]\)", following):
                starts.append((index, heading))
        for (index, office), nxt in zip(starts, [s[0] for s in starts[1:]] + [len(lines)], strict=True):
            section = lines[index + 1 : nxt]
            head = " ".join(line for line in section[:8] if "(" in line).replace("(Hank)", "«Hank»")
            names = [(re.sub(r"\s+", " ", m[1]).strip().replace("«Hank»", "(Hank)"), PARTIES[m[2]]) for m in re.finditer(r"([A-Za-z.,'«» \-]+?)\s*\(([A-Z])\)", head)]
            names = [(re.sub(r"^\*?Elected\s*", "", n).strip(), p) for n, p in names]
            rows += table(section, names, office, year, f"{year} {office}")
    for year, (_, sheets) in SHEETS.items():
        for office, name in sheets.items():
            lines = text(f"{SITE}{year}/{year}%20{name}%20Gen.pdf").splitlines()
            header = next(i for i, line in enumerate(lines) if line.strip().startswith("County"))
            first = re.split(r"\s{2,}", lines[header].strip())[1:]
            party_line = next(line for line in lines[header + 1 : header + 4] if re.search(r"Democrat|Republican|Libertarian|Constitution|Mountain|Independent", line))
            parties = re.split(r"\s{2,}", party_line.strip())
            names = [(n.strip(), PARTIES[p]) for n, p in zip(first, parties, strict=True)]
            rows += table(lines[header + 1 :], names, office, year, f"{year} {office}")
    frame = pl.DataFrame(rows, schema=["year", "office", "candidate", "party", "county", "votes"], orient="row")
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    days = {year: day for year, (day, _) in {**BOOKS, **{y: (d, None) for y, (d, _) in SHEETS.items()}}.items()}
    frame = frame.select(
        year=pl.col("year").cast(pl.Int32),
        day=pl.col("year").replace_strict({y: str(d) for y, d in days.items()}),
        state=pl.lit("WV"),
        title=pl.col("office").replace_strict(OFFICES),
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county=pl.col("county").map_elements(lambda c: codes[re.sub(r"[^a-z]", "", c.lower())], return_dtype=pl.String),
        candidate=pl.col("candidate").str.to_uppercase(),
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(55),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "candidate", "party").agg(pl.col("votes").sum()).sort("year", "title", "votes").rows())


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
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
        for url in addresses():
            cs.artifact(raw.cached(SOURCE, url), source)
        return {"WV": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="West Virginia's 1992, 1996, 2000, and 2004 secretary of state, auditor, treasurer, commissioner of "
        "agriculture, and attorney general by county from the Secretary of State's official returns",
        cite=cite,
        method=METHOD,
    )
