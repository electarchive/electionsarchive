import re
import subprocess
from datetime import date
from html import unescape
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, ut_sos
from ea.store import DATA, connect

SOURCE = "us.ut_leg_pdf"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2004: date(2004, 11, 2), 2006: date(2006, 11, 7)}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>')


def cached(year: int) -> raw.Artifact:
    stamp, path = ("20110927154946", "General/2002Gen.pdf") if year == 2002 else ut_sos.FILES[(year, "general")]
    return raw.cached(ut_sos.SOURCE, ut_sos.WAYBACK.format(stamp, ut_sos.SITE + path))


def fetch() -> None:
    ut_sos.fetch()
    raw.fetch(
        ut_sos.SOURCE, ut_sos.WAYBACK.format("20110927154946", ut_sos.SITE + "General/2002Gen.pdf"), suffix=".pdf"
    )


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '49%%'"""
            ).fetchall()
        }
        held: dict[tuple[date, int], list[tuple[str, str]]] = {}
        for key, label, votes in conn.execute(
            """select x.value, o.ballot_label, sum(v.value)::bigint from external_id x
               join result_set r on r.contest_id = x.entity_id and r.is_default
               join result_value v on v.result_set_k = r.k and v.measure = 1
               join contest_option o on o.k = v.option_k and o.kind = 'candidate'
               where x.scheme = 'us.contest' and x.value ~ '^20(00|02|04|06)-11-0[0-9]:UT:(house|senate):\\d+:g$'
               group by 1, 2"""
        ).fetchall():
            day = date.fromisoformat(key[:10])
            held.setdefault((day, int(votes)), []).append((key.split(":", 2)[2], label))
    rows: list[tuple[date, str, str, str, int]] = []
    for year, day in DAYS.items():
        html = subprocess.run(
            ["pdftotext", "-bbox", str(cached(year).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        pages = [
            [(float(x1), float(y), unescape(text)) for _, y, x1, text in WORD.findall(page)]
            for page in html.split("<page ")[1:]
        ]
        grids: list[tuple[float, dict[str, float]]] = []
        for words in pages:
            lines: dict[float, list[str]] = {}
            for _, y, text in words:
                if re.fullmatch(r"[A-Za-z]+", text):
                    lines.setdefault(round(y, 1), []).append(text)
            labels = sorted((y, "".join(texts).lower()) for y, texts in lines.items())
            places: dict[str, float] = {}
            for (y, label), following in zip(labels, [*labels[1:], (0.0, "")], strict=True):
                if label == "washingto" and following[1] == "n":
                    places[codes["washington"]] = following[0]
                elif (code := codes.get(label)) is not None:
                    places[code] = y
            total = next((y for y, label in labels if label == "total"), None)
            if total is not None and len(places) >= 25 and (total, places) not in grids:
                grids.append((total, places))
        for number, words in enumerate(pages, 1):
            if not re.search(r"(?i)senate|house", " ".join(text for _, _, text in words)):
                continue
            figures = [(x, y, int(text.replace(",", ""))) for x, y, text in words if re.fullmatch(r"\d[\d,]*", text)]
            for total, places in grids:
                printed = sorted((x, value) for x, y, value in figures if abs(y - total) < 2)
                if len(printed) < 3:
                    continue
                for anchor, expected in printed:
                    cells = [
                        (code, value)
                        for code, place in places.items()
                        for x, y, value in figures
                        if abs(y - place) < 2 and abs(x - anchor) <= 4
                    ]
                    found = held.get((day, expected), [])
                    if sum(value for _, value in cells) != expected or len(cells) < 1:
                        continue
                    if len(found) == 1:
                        key, label = found[0]
                        rows.extend((day, key, code, label, value) for code, value in cells)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "chamber", "code", "candidate", "votes"], orient="row").unique().select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("UT"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.lit("county"),
        candidate=pl.col("candidate").str.to_uppercase(),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes="votes",
        source=pl.lit("UT"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        filled = [
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status <> 'withdrawn'
                   where x.scheme = 'us.contest' and x.value ~ '^20(00|02|04|06)-11-0[0-9]:UT:(house|senate):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k and u.scheme_code in ('us.precinct', 'us.county', 'us.county_part'))"""
            ).fetchall()
        ]
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's 2000, 2004, and 2006 State House and Senate generals by county from the Office of "
            "Elections' official canvasses, columns identified by their printed totals",
            polity="US-UT",
        )
        source = cs.source(ut_sos.PUBLISHER, ut_sos.TITLE)
        if source in cs.minted:
            raise ValueError(f"no source {ut_sos.TITLE}")
        for year in DAYS:
            cs.artifact(cached(year), source)
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit("UT"), "chamber", separator=":").is_in(filled)
        )
        medsl.attach(cs, conn, frame, {"UT": source}, strict=True)
        return cs.write()
