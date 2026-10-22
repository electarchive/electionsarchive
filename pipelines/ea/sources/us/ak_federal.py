import csv
import html
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ak_federal"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.elections.alaska.gov/results/"
FILES = {
    date(2014, 8, 19): "14PRIM/data/results-precinct.txt",
    date(2016, 8, 16): "16PRIM/data/resultsbyprct.txt",
    date(2018, 8, 21): "18PRIM/data/resultsbyprecinct.txt",
    date(2020, 8, 18): "20PRIM/data/sovc/resultsbyprecinct20.txt",
}
SOVC = {date(2004, 8, 24): "04PRIM", date(2006, 8, 22): "06PRIM", date(2008, 8, 26): "08PRIM", date(2010, 8, 24): "10PRIM", date(2012, 8, 28): "12PRIM"}
SOVC_URL = BASE + "{}/data/sovc/hd{}.pdf"
PARTIES = {"DEM": "democratic", "REP": "republican", "LIB": "libertarian", "AD": "democratic"}
STATS = {
    "Number of Precincts",
    "Number of Precincts for Race",
    "Number of Precincts Reporting",
    "Registered Voters",
    "Times Counted",
}
PUBLISHER = "Alaska Division of Elections"
TITLE = "Primary election results by precinct"


def fetch() -> None:
    urls = [BASE + path for path in FILES.values()]
    urls += [SOVC_URL.format(folder, number) for folder in SOVC.values() for number in range(1, 41)]
    for url in urls:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url], capture_output=True, check=True
            ).stdout
            pdf = url.endswith(".pdf")
            raw.store(
                SOURCE,
                url,
                body,
                suffix=".pdf" if pdf else ".txt",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf" if pdf else "text/plain",
            )


def parse() -> None:
    rows = []
    for day, path in FILES.items():
        totals: dict[tuple[str, str], int] = {}
        printed: dict[tuple[str, str], int] = {}
        lines = raw.cached(SOURCE, BASE + path).path.read_text(encoding="latin-1").splitlines()
        for row in csv.reader(lines):
            if len(row) < 8 or not row[1].strip().upper().replace(".", "").startswith(
                ("US SENATOR", "US REPRESENTATIVE")
            ):
                continue
            place, race, ballot, _, name, party, column, votes = (cell.strip() for cell in row[:8])
            race = race.upper().replace(".", "")
            if day.year >= 2020:
                party = ballot
            if name in STATS or column != "Total" or party not in PARTIES:
                continue
            office = "senate" if race.startswith("US SENATOR") else "house"
            chamber = f"{office}:{PARTIES[party]}"
            key = (chamber, name.upper())
            if place == "Election Total":
                printed[key] = int(votes)
            elif found := re.match(r"(\d\d)-", place) or re.match(r"District (\d+)\s*- ", place):
                totals[key] = totals.get(key, 0) + int(votes)
                rows.append(
                    (
                        day.year,
                        chamber,
                        day,
                        False,
                        "AK",
                        0 if office == "house" else None,
                        f"{day.isoformat()}:{int(found[1]):02}",
                        name.upper(),
                        "district",
                        int(votes),
                    )
                )
            elif place.startswith("HD99"):
                totals[key] = totals.get(key, 0) + int(votes)
                rows.append(
                    (
                        day.year,
                        chamber,
                        day,
                        False,
                        "AK",
                        0 if office == "house" else None,
                        "AK:99",
                        name.upper(),
                        "aggregate",
                        int(votes),
                    )
                )
        for key in set(totals) | set(printed):
            if printed and totals.get(key) != printed.get(key):
                print(day, key, "sum", totals.get(key), "printed", printed.get(key))
    for day, folder in SOVC.items():
        for number in range(1, 41):
            blocks: list[list] = []
            pdf = raw.cached(SOURCE, SOVC_URL.format(folder, number)).path
            markup = subprocess.run(
                ["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True
            ).stdout
            for sheet in markup.split("<page ")[1:]:
                words = [
                    (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
                    for x0, y0, x1, y1, text in re.findall(
                        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', sheet
                    )
                ]
                total = [w for w in words if w[4] == "Total" and w[0] < 50 and w[1] > 150]
                if not total:
                    continue
                numbers = [
                    w[4].replace(",", "")
                    for w in sorted(
                        (w for w in words if abs(w[1] - total[0][1]) < 1 and w[0] > 100), key=lambda w: w[0]
                    )
                ]
                stacks: dict[float, list[tuple[float, float, float, float, str]]] = {}
                for w in words:
                    if 7.1 < w[2] - w[0] < 7.3 and w[1] > 100 and w[3] < total[0][1]:
                        stacks.setdefault(round(w[0]), []).append(w)
                heads = [
                    (x, " ".join(w[4] for w in sorted(stack, key=lambda w: -w[3])))
                    for x, stack in sorted(stacks.items())
                ]
                if len(heads) != len(numbers):
                    print(day, number, "columns", len(heads), "numbers", len(numbers))
                    continue
                titles: list[list[float | str]] = []
                for w in sorted((w for w in words if 80 < w[1] < 140 and w[0] > 100), key=lambda w: (round(w[1]), w[0])):
                    if titles and abs(w[1] - titles[-1][3]) < 2 and w[0] - titles[-1][1] < 6:
                        titles[-1][1] = w[2]
                        titles[-1][2] += f" {w[4]}"
                    else:
                        titles.append([w[0], w[2], w[4], w[1]])
                starts = [i for i, (_, label) in enumerate(heads) if label.startswith("Reg")]
                if starts and starts[0] > 0 and blocks:
                    blocks[-1][2].extend(label for _, label in heads[: starts[0]])
                    blocks[-1][3].extend(int(n) for n in numbers[: starts[0]])
                for k, begin in enumerate(starts):
                    end = starts[k + 1] if k + 1 < len(starts) else len(heads)
                    left, right = heads[begin][0], heads[end - 1][0] + 10
                    best = max(
                        (t for t in titles if not re.fullmatch(r"HOUSE DISTRICT \d+", str(t[2]))),
                        key=lambda t: min(right, t[1]) - max(left, t[0]),
                        default=None,
                    )
                    cut = next((i for i in range(begin, end) if heads[i][1].startswith("Total")), None)
                    if cut is None or best is None or not str(best[2]).startswith(("US SENAT", "US REPRESENTATIVE")):
                        continue
                    blocks.append(
                        [
                            str(best[2]),
                            int(numbers[cut]),
                            [label for _, label in heads[cut + 1 : end]],
                            [int(n) for n in numbers[cut + 1 : end]],
                        ]
                    )
            for title, printed, names, cells in blocks:
                if sum(cells) != printed:
                    print(day, number, title, "sum", sum(cells), "printed", printed)
                    continue
                office = "senate" if title.startswith("US SENAT") else "house"
                for label, votes in zip(names, cells, strict=True):
                    found = re.fullmatch(r"(.*) \((\w+)\)", label)
                    if found is None or found[2] not in PARTIES:
                        continue
                    rows.append(
                        (
                            day.year,
                            f"{office}:{PARTIES[found[2]]}",
                            day,
                            False,
                            "AK",
                            0 if office == "house" else None,
                            f"{day.isoformat()}:{number:02}",
                            found[1].strip().upper(),
                            "district",
                            votes,
                        )
                    )
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
            "kind": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate").agg(
        pl.col("votes").sum()
    ).with_columns(measure=pl.lit(1, pl.Int16), writein=pl.lit(False), source=pl.lit("AK")).write_parquet(
        PARSED / "districts.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "districts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, and 2020 U.S. Senate and House primaries by House district",
            polity="US-AK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.elections.alaska.gov/election-results/",
            license="Public record",
        )
        for url in [BASE + path for path in FILES.values()] + [
            SOVC_URL.format(folder, number) for folder in SOVC.values() for number in range(1, 41)
        ]:
            cs.artifact(raw.cached(SOURCE, url), source)
        state, parent = conn.execute(
            """select p.id, u.id from reporting_unit u join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.state' and p.iso_code = 'US-AK'"""
        ).fetchone()
        for code, day in frame.filter(pl.col("kind") == "district").select("code", "day").unique().iter_rows():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.ak_house_district", code),
                polity_id=state,
                scheme_code="us.ak_house_district",
                code=code,
                parent_id=parent,
                area_relation="equals",
                area_id=cs.area("us.sldl", f"AK:{int(code[-2:])}"),
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        medsl.attach(cs, conn, frame, {"AK": source}, strict=True)
        return cs.write()
