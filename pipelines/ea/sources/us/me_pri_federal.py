import html
import re
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.me_federal import COUNTIES, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.me_pri_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
D10, D18 = date(2010, 6, 8), date(2018, 6, 12)
FILES = [
    ("2015", "http://www.maine.gov:80/sos/cec/elec/results/2010-11/tabscg1dem0610.html", D10, "house:democratic", 1),
    ("2015", "http://www.maine.gov:80/sos/cec/elec/results/2010-11/tabscg1rep0610.html", D10, "house:republican", 1),
    ("2015", "http://www.maine.gov:80/sos/cec/elec/results/2010-11/tabscg2dem0610.html", D10, "house:democratic", 2),
    ("2015", "http://www.maine.gov:80/sos/cec/elec/results/2010-11/tabscg2rep0610.html", D10, "house:republican", 2),
    ("2020", "https://www.maine.gov/sos/cec/elec/results/2018/uscongd1d.xlsx", D18, "house:democratic", 1),
    ("2020", "https://www.maine.gov/sos/cec/elec/results/2018/uscongd1r.xlsx", D18, "house:republican", 1),
    ("2020", "https://www.maine.gov/sos/cec/elec/results/2018/uscongd2r.xlsx", D18, "house:republican", 2),
    ("2020", "https://www.maine.gov/sos/cec/elec/results/2018/ussend.xlsx", D18, "senate:democratic", None),
    ("2020", "https://www.maine.gov/sos/cec/elec/results/2018/ussenr.xlsx", D18, "senate:republican", None),
]
NAMES = {
    "ANDROSCOGGIN": "AND",
    "AROOSTOOK": "ARO",
    "CUMBERLAND": "CUM",
    "FRANKLIN": "FRA",
    "HANCOCK": "HAN",
    "KENNEBEC": "KEN",
    "KNOX": "KNO",
    "LINCOLN": "LIN",
    "OXFORD": "OXF",
    "PENOBSCOT": "PEN",
    "PISCATAQUIS": "PIS",
    "SAGADAHOC": "SAG",
    "SOMERSET": "SOM",
    "WALDO": "WAL",
    "WASHINGTON": "WAS",
    "YORK": "YOR",
}
MISCODED = {"MOLUNKUS": "ARO"}
UNLISTED = {
    "BANCROFT": "ARO",
    "BROOKTON": "WAS",
    "DTT9T10": "HAN",
    "EDMUNDS": "WAS",
    "INDIANPURCHASE": "PEN",
    "MADRID": "FRA",
    "NO27": "WAS",
    "NO8SD": "HAN",
    "OXBOWPLT": "ARO",
    "PENOBNATVOTINGDIS": "PEN",
    "PLEASANTPTVOTINGDIS": "WAS",
    "ROCKWOOD": "SOM",
    "SAINTAGATHA": "ARO",
    "T1R9": "PIS",
}


def fetch() -> None:
    for stamp, url, *_ in FILES:
        raw.fetch(SOURCE, WAYBACK.format(stamp, url), suffix=Path(url).suffix)


def place(text: str) -> str:
    text = re.sub(r"\bST\b\.?", "SAINT", text.upper()).replace("PLANTATION", "PLT")
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"\b(?:TOWNSHIPS?|TWPS?|ISLAND)\b", "", text))


def parse() -> None:
    lines: list[tuple[date, str, int | None, str, str, int]] = []
    homes: dict[str, str] = dict(UNLISTED)
    for stamp, url, day, chamber, district in FILES:
        path = raw.cached(SOURCE, WAYBACK.format(stamp, url)).path
        if day == D10:
            continue
        table = [
            [(cell or "").strip() for cell in row]
            for row in fastexcel.read_excel(path).load_sheet(0, header_row=None, dtypes="string").to_polars().rows()
        ]
        county = table[0].index("CTY")
        names = {
            index: cell
            for index, cell in enumerate(table[0])
            if index > county + 1 and cell and cell.upper() not in ("BLANK", "TOTAL BALLOTS CAST")
        }
        counted: dict[tuple[str, str], int] = {}
        for row in table[1:]:
            label = row[county + 1].upper()
            if row[county] in COUNTIES:
                code = MISCODED.get(place(label), row[county])
                homes[place(label)] = code
                for index, name in names.items():
                    lines.append((day, chamber, district, code, name, int(row[index] or 0)))
                    counted[(code, name)] = counted.get((code, name), 0) + int(row[index] or 0)
            elif "UOCAVA" in label:
                for index, name in names.items():
                    lines.append((day, chamber, district, "uocava", name, int(row[index] or 0)))
            elif (found := re.fullmatch(r"(\w+) TOTAL", label)) and found[1] in NAMES:
                for index, name in names.items():
                    if counted.get((NAMES[found[1]], name), 0) != int(row[index] or 0):
                        raise ValueError(f"{url}: {found[1]} {name} towns {counted.get((NAMES[found[1]], name))}")
    for stamp, url, day, chamber, district in FILES:
        if day != D10:
            continue
        names: list[str] = []
        total = 0
        for row in re.findall(
            r"(?is)<tr.*?</tr>", raw.cached(SOURCE, WAYBACK.format(stamp, url)).path.read_text("latin-1")
        ):
            cells = [
                [
                    line.strip()
                    for line in html.unescape(re.sub(r"(?s)<[^>]+>", "\n", cell)).splitlines()
                    if line.strip()
                ]
                for cell in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row)
            ]
            flat = [" ".join(" ".join(cell).split()) for cell in cells]
            if not flat or flat[0] == "Municipality":
                continue
            if flat[0] == "":
                names = [" ".join(" ".join(cell[:-1]).split()) for cell in cells[1:]]
            elif flat[0].lower().startswith("total"):
                total = sum(int(votes.replace(",", "")) for votes in flat[1:])
                parsed = sum(votes for d, c, n, _, _, votes in lines if (d, c, n) == (day, chamber, district))
                if total != parsed:
                    raise ValueError(f"{url}: towns {parsed}, printed {total}")
            else:
                code = "uocava" if "UOCAVA" in flat[0].upper() else homes.get(place(flat[0]))
                if code is None:
                    raise ValueError(f"{url}: no county for {flat[0]}")
                for name, votes in zip(names, flat[1:], strict=True):
                    lines.append((day, chamber, district, code, name, int(votes.replace(",", ""))))
        if not total:
            raise ValueError(f"{url}: no total row")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            lines,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "county": pl.String,
                "candidate": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("ME"),
            code=pl.when(pl.col("county") == "uocava")
            .then(pl.lit("ME:uocava"))
            .otherwise(pl.col("county").replace_strict(COUNTIES, default=None)),
            kind=pl.when(pl.col("county") == "uocava").then(pl.lit("aggregate")).otherwise(pl.lit("county")),
            candidate=pl.col("candidate").str.to_uppercase(),
            measure=pl.lit(1, pl.Int16),
            writein=pl.col("candidate").str.contains("WRITE"),
        )
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.lit("ME"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's 2010 U.S. House and 2018 U.S. Senate and House primaries by county",
            polity="US-ME",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {TITLE}")
        for stamp, url, *_ in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, url)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"ME": source}, strict=True)
        return cs.write()
