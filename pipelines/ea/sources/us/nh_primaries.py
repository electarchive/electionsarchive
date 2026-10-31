import re
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.nh_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = Path.home() / "Downloads" / "ea-manual" / "nh"
BASE = "https://www.sos.nh.gov/sites/g/files/ehbemt561/files/inline-documents/sonh/"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2022_Gazetteer/2022_Gaz_cousubs_national.zip"
DAY = date(2022, 9, 13)
FILES = {
    "2022-sp-uss-democratic_4.xls": ("senate", None, "democratic"),
    "2022-sp-uss-republican_1.xls": ("senate", None, "republican"),
    "2022-sp-congressional-district-1-democratic.xlsx": ("house", 1, "democratic"),
    "2022-sp-congressional-district-1-republican_0.xlsx": ("house", 1, "republican"),
    "2022-sp-congressional-district-2-democratic_1.xlsx": ("house", 2, "democratic"),
    "2022-sp-congressional-district-2-republican_0.xlsx": ("house", 2, "republican"),
}
OVERRIDES = {
    "atandgilacgt": "atkinsonandgilmantonacademy",
    "thompandmesspur": "thompsonandmeserves",
    "wentworths": "wentworth",
}
NAMES = {
    "Vikram M., r *": "Vikram Mansharamani, r",
    "Tejasinha S. r, **": "Tejasinha Sivalingam, r",
}
PUBLISHER = "New Hampshire Secretary of State"
TITLE = "2022 state primary election results by town"


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTEER, suffix=".zip")
    for name in FILES:
        url = BASE + name
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / name
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/vnd.ms-excel",
            )


def parse() -> None:
    import io
    import zipfile

    archive = zipfile.ZipFile(raw.cached(SOURCE, GAZETTEER).path)
    gaz = pl.read_csv(
        io.BytesIO(archive.read("2022_Gaz_cousubs_national.txt")),
        separator="\t",
        infer_schema_length=0,
        encoding="utf8-lossy",
    ).filter(pl.col("USPS") == "NH")

    def norm(text: str) -> str:
        text = re.sub(r"\s*-?\s*ward\s*\d+\*?\s*$", "", text.strip().lower().replace("&", " and "))
        text = re.sub(r"['\u2019]", "", text)
        words = text.split()
        while words and words[-1] in {"town", "city", "township", "location", "grant", "purchase"} and len(words) > 1:
            words.pop()
        return OVERRIDES.get(re.sub(r"[^a-z]", "", "".join(words)), re.sub(r"[^a-z]", "", "".join(words)))

    counties: dict[str, str] = {}
    for geoid, name in zip(gaz["GEOID"], gaz[gaz.columns[3]], strict=True):
        counties.setdefault(norm(name), f"33{geoid[2:5]}")
    with connect() as conn:
        county_codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
    rows = []
    for name, (kind, district, party) in FILES.items():
        sheet = pl.read_excel(raw.cached(SOURCE, BASE + name).path, has_header=False, infer_schema_length=0).rows()
        header = next(i for i, row in enumerate(sheet) if any(c and re.search(r", [dr]\b", c.strip()) for c in row[1:]))
        names = [NAMES.get(" ".join((c or "").split()), " ".join((c or "").split())) for c in sheet[header]]
        by_county = kind == "senate"
        unmatched = set()
        for row in sheet[header + 1 :]:
            town = row[0]
            if town is None or town.strip().upper().startswith("TOTAL") or not town.strip() or town.startswith("*"):
                continue
            if by_county:
                if re.sub(r"[^a-z]", "", town.lower()) not in county_codes:
                    break
                code = county_codes[re.sub(r"[^a-z]", "", town.lower())]
            else:
                code = counties.get(norm(town))
                if code is None:
                    unmatched.add(town)
                    continue
            for index in range(1, len(names)):
                cell = row[index]
                if not names[index] or cell is None or not cell.strip():
                    continue
                tagged = re.search(r",\s*([dr])$", names[index])
                candidate = re.sub(r",\s*[dr]$", "", names[index]).upper()
                other = tagged is not None and tagged[1] != party[0]
                rows.append(
                    (
                        2022,
                        f"{kind}:{party}",
                        DAY,
                        False,
                        "NH",
                        district,
                        code,
                        candidate,
                        1,
                        other or candidate.startswith("SCATTER"),
                        int(float(cell)),
                    )
                )
        if unmatched:
            print(name, "unmatched:", sorted(unmatched))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein").agg(
        pl.col("votes").sum()
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NH")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2022 U.S. Senate and House primaries by county, summed from town returns",
            polity="US-NH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.nh.gov/elections/2022-election-results/2022-state-primary-election-results",
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NH": source}, strict=True)
        return cs.write()
