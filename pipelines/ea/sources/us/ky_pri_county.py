import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ky_pri_county"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = Path.home() / "Downloads" / "kyp_all.json"
SITE = "https://elect.ky.gov"
BASE = "/SiteCollectionDocuments/Election%20Results/2000-2009/"
DAYS = {
    "2000/00Pri_Statewide%20by%20County.txt": date(2000, 5, 23),
    "2002/Primary%20Election/pristatebycounty.txt": date(2002, 5, 28),
    "2004/Primary%20Election/statewidebyCOUNTY.txt": date(2004, 5, 18),
    "2006/Primary%20Election/stateresultsbyCOUNTY.txt": date(2006, 5, 16),
    "2008/Primary%20Election/STATEwide%20by%20Candidate%20Pri%2008.txt": date(2008, 5, 20),
}
CODES = [
    "ADAI", "ALLE", "ANDE", "BALL", "BARR", "BATH", "BELL", "BOON", "BOUR", "BOYD", "BOYL", "BRAC", "BREA", "BREC", "BULL",
    "BUTL", "CALD", "CALL", "CAMP", "CARL", "CARR", "CART", "CASE", "CHRI", "CLAR", "CLAY", "CLIN", "CRIT", "CUMB", "DAVI",
    "EDMO", "ELLI", "ESTI", "FAYE", "FLEM", "FLOY", "FRAN", "FULT", "GALL", "GARR", "GRAN", "GRAV", "GRAY", "GREE", "GREU",
    "HANC", "HARD", "HARL", "HARR", "HART", "HEND", "HENR", "HICK", "HOPK", "JACK", "JEFF", "JESS", "JOHN", "KENT", "KNOT",
    "KNOX", "LARU", "LAUR", "LAWR", "LEE", "LESL", "LETC", "LEWI", "LINC", "LIVI", "LOGA", "LYON", "MCCK", "MCCR", "MCLE",
    "MADI", "MAGO", "MARI", "MARS", "MART", "MASO", "MEAD", "MENI", "MERC", "METC", "MONR", "MONT", "MORG", "MUHL", "NELS",
    "NICH", "OHIO", "OLDH", "OWEN", "OWSL", "PEND", "PERR", "PIKE", "POWE", "PULA", "ROBE", "ROCK", "ROWA", "RUSS", "SCOT",
    "SHEL", "SIMP", "SPEN", "TAYL", "TODD", "TRIG", "TRIM", "UNIO", "WARR", "WASH", "WAYN", "WEBS", "WHIT", "WOLF", "WOOD",
]  # fmt: skip
OFFICE = re.compile(r"OFFICE: \S+\s+U\.S\. (SENATE|PRESIDENT|REPRESENTATIVE (\d+)(?:ST|ND|RD|TH) DIST)\s*$")
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Primary election results"


def fetch() -> None:
    files = json.loads(MANUAL.read_text())
    for key, text in files.items():
        path = unquote(key).replace(" ", "%20")
        if path.removeprefix(BASE) in DAYS:
            raw.store(
                SOURCE,
                SITE + path,
                text.encode(),
                suffix=".txt",
                retrieved_at=datetime.fromtimestamp(MANUAL.stat().st_mtime, UTC),
                media_type="text/plain",
            )


def parse() -> None:
    blocks: list[tuple[date, str, int | None, dict[str, dict[str, int]]]] = []
    for path, day in DAYS.items():
        text = raw.cached(SOURCE, SITE + BASE + path).path.read_text(errors="replace")
        current = None
        columns: list[str] = []
        for line in text.splitlines():
            if "OFFICE:" in line:
                found = OFFICE.search(line.rstrip())
                current = (
                    (day, {"SENATE": "senate", "PRESIDENT": "president"}.get(found[1], "house"), int(found[2]) if found[2] else None, {})
                    if found
                    else None
                )
                if current:
                    blocks.append(current)
                columns = []
            elif current is None:
                continue
            elif not line.strip():
                columns = []
            elif re.match(r"\s{20,}\*[A-Z]", line):
                columns = re.findall(r"\*([A-Z]+)", line)
            elif columns and line.strip() and re.fullmatch(r"[\d,\s]*", line[34:]) and line[:34].strip():
                name = re.sub(r"\s+", " ", line[:34].replace('"', "")).strip()
                cells = [int(value.replace(",", "") or 0) for value in re.findall(r"[\d,]+", line[34:])]
                if len(cells) != len(columns):
                    cells = [0] * len(columns)
                    for match in re.finditer(r"[\d,]+", line[34:]):
                        cells[min(len(columns) - 1, (match.end() - 1) // 8)] = int(match[0].replace(",", ""))
                for code, value in zip(columns, cells, strict=True):
                    current[3].setdefault(name, {})[code] = value
    with connect() as conn:
        known: dict[str, list[int]] = {}
        for contest, total in conn.execute(
            """select x.value, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id and o.kind in ('candidate', 'ticket', 'uncommitted')
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^(2000-05-23|2002-05-28|2004-05-18|2006-05-16|2008-05-20):KY:'
               group by x.value, o.id"""
        ).fetchall():
            known.setdefault(contest, []).append(total)
    keep: list[pl.DataFrame] = []
    for day, chamber, district, candidates in blocks:
        sums = sorted(total for total in (sum(counts.values()) for counts in candidates.values()) if total > 0)
        base = f"{day}:KY:" + (f"house:{district}" if district else chamber)
        party = next((p for p in ("democratic", "republican") if sorted(t for t in known.get(f"{base}:{p}", []) if t > 0) == sums), None)
        if party is None:
            print(f"{base}: county sums {sums} match no party's FEC totals")
            continue
        keep.append(
            pl.DataFrame(
                [
                    (day, f"{chamber}:{party}", district, name, f"21{2 * CODES.index(code) + 1:03}", votes)
                    for name, counts in candidates.items()
                    for code, votes in counts.items()
                ],
                schema={"day": pl.Date, "chamber": pl.String, "district": pl.Int16, "candidate": pl.String, "code": pl.String, "votes": pl.Int64},
                orient="row",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(keep).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("KY"),
        measure=pl.lit(1, dtype=pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        kind=pl.lit("county"),
        source=pl.lit("KY"),
    ).write_parquet(PARSED / "counties.parquet")
    print(f"{len(keep)} contests kept")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's 2000–2008 U.S. House and Senate primaries by county",
            polity="US-KY",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elect.ky.gov/results/Pages/default.aspx",
            license="Public record",
        )
        for path in DAYS:
            cs.artifact(raw.cached(SOURCE, SITE + BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"KY": source}, strict=True)
        return cs.write()
