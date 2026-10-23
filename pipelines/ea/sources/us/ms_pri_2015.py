import html
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ms_pri_2015"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.sos.ms.gov/elections/electionResults/"
FILES = {
    "2015%20Democratic%20Primary%20Certified%20Results.pdf": (date(2015, 8, 4), "d"),
    "2015%20Republican%20Primary%20Certified%20Results.pdf": (date(2015, 8, 4), "r"),
}
PUBLISHER = "Mississippi Secretary of State"
TITLE = "Official recapitulation of the party primaries"
TITLES = re.compile(r"State (Senate|House) (?:Rep\. )?Dist(?:rict|\.) (\d+)")
OTHER = re.compile(
    r"\b(District|Dist\.?|Governor|General|Secretary|Auditor|Treasurer|Commissioner|Judge|Court|Insurance|Agriculture"
    r"|Transportation|Service|Sheriff|Supervisor|Chancery|Circuit|Justice|Clerk|Collector|Superintendent|Attorney"
    r"|Position|Post|Place)\b"
)
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.removesuffix(", MS").removesuffix(" County").lower()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '28%%' and u.valid @> '2015-01-01'::date"""
            ).fetchall()
        }
        keys = [
            key
            for (key,) in conn.execute(
                """select value from external_id
                   where scheme = 'us.contest' and value ~ '^2015-08-04:MS:(house|senate):'"""
            ).fetchall()
        ]
    codes["jeffdavis"] = codes["jeffersondavis"]
    codes |= {"total": "TOTAL"}
    rows = []
    for name, (day, party) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-bbox", str(raw.cached(SOURCE, BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        votes: dict[tuple[str, str], dict[str, int]] = {}
        contest = None
        for page in text.split("<page ")[1:]:
            lines: dict[int, list[tuple[float, float, str]]] = {}
            for x0, y0, x1, word in WORD.findall(page):
                lines.setdefault(round(float(y0)), []).append((float(x0), float(x1), html.unescape(word)))
            ordered = [sorted(lines[y]) for y in sorted(lines)]
            header = next((line for line in ordered if line[0][2] == "Primary" and len(line) > 2), None)
            if header is None:
                continue
            columns: list[tuple[float, float, str]] = []
            words = header[1:]
            index = 0
            while index < len(words):
                x0, x1, word = words[index]
                pair = re.sub(r"[^a-z]", "", (word + (words[index + 1][2] if index + 1 < len(words) else "")).lower())
                if index + 1 < len(words) and pair in codes:
                    columns.append((x0, words[index + 1][1], codes[pair]))
                    index += 2
                    continue
                if re.sub(r"[^a-z]", "", word.lower()) in codes:
                    columns.append((x0, x1, codes[re.sub(r"[^a-z]", "", word.lower())]))
                index += 1
            left = min(x0 for x0, _, _ in columns) - 4
            for line in ordered[ordered.index(header) + 1 :]:
                label = " ".join(word for x0, x1, word in line if x1 < left)
                found = TITLES.fullmatch(label)
                if found:
                    contest = f"{found[1].lower()}:{int(found[2])}"
                    continue
                numbers = [(x0, x1, word) for x0, x1, word in line if x1 >= left and re.fullmatch(r"[\d,]+", word)]
                if not numbers and OTHER.search(label):
                    contest = None
                if contest is None or not label or not numbers:
                    continue
                cells = votes.setdefault((contest, label), {})
                for x0, x1, word in numbers:
                    county = min(columns, key=lambda column: abs((column[0] + column[1]) / 2 - (x0 + x1) / 2))[2]
                    cells[county] = int(word.replace(",", ""))
        for (contest, candidate), cells in votes.items():
            matched = [key for key in keys if key.startswith(f"{day}:MS:{contest}:{party}pf")]
            total = cells.pop("TOTAL", None)
            if len(matched) != 1 or total is None or sum(cells.values()) != total:
                print(
                    f"{day} {contest} {party} {candidate}: keys {matched}, counties {sum(cells.values())}, total {total}"
                )
                continue
            rows.extend((day, matched[0].split(":", 2)[2], code, candidate, value) for code, value in cells.items())
    frame = pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("day").agg(pl.col("chamber").n_unique(), pl.len()).rows())


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mississippi's 2015 State House and Senate primaries by county from the Secretary of State's certified "
            "recapitulations of the Democratic and Republican primaries",
            polity="US-MS",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.ms.gov/elections-voting/election-results",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "counties.parquet").with_columns(
                year=pl.lit(2015, pl.Int32),
                special=pl.lit(False),
                state=pl.lit("MS"),
                district=pl.lit(None, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                kind=pl.lit("county"),
                source=pl.lit("MS"),
            ),
            {"MS": source},
            strict=True,
        )
        return cs.write()
