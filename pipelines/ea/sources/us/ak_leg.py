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
from ea.sources.us.ak_federal import STATS
from ea.store import DATA, connect

SOURCE = "us.ak_leg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.elections.alaska.gov/results/"
SOVC = {
    date(2006, 11, 7): "06GENR/data/sovc",
    date(2008, 11, 4): "08GENR/data/sovc",
    date(2010, 11, 2): "10GENR/data/sovcOF",
    date(2012, 11, 6): "12GENR/data/sovc",
}
TEXT = {
    date(2014, 8, 19): "14PRIM/data/results-precinct.txt",
    date(2014, 11, 4): "14GENR/data/results-precinct.txt",
}
PRIMARY = {"DEM": "dp", "REP": "rp", "LIB": "liberp"}
KINDS = {"Absentee": "absentee", "Question": "question", "Early Voting": "early"}
PUBLISHER = "Alaska Division of Elections"
TITLE = "Statements of votes cast by House district and precinct results"


def urls() -> list[str]:
    return [BASE + path for path in TEXT.values()] + [
        f"{BASE}{folder}/hd{number}.pdf" for folder in SOVC.values() for number in range(1, 41)
    ]


def fetch() -> None:
    for url in urls():
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
    rows: list[tuple] = []
    names: dict[str, str] = {}
    for day, path in TEXT.items():
        lines = raw.cached(SOURCE, BASE + path).path.read_text(encoding="latin-1").splitlines()
        for row in csv.reader(lines, escapechar="\\"):
            cells = [cell.strip() for cell in row]
            if len(cells) < 6:
                continue
            if day.month == 11:
                place, race, name, party, column, votes = cells[:6]
            else:
                place, race, _, _, name, party, column, votes = cells[:8]
            found = re.fullmatch(r"(HOUSE|SENATE) DISTRICT (\w+)(?: \((?:ADL|R)\))?", race)
            if found is None or column != "Total" or name in STATS or not votes:
                continue
            seat = found[2].lower() if found[1] == "SENATE" else str(int(found[2]))
            writein = name.lower().startswith("write")
            if day.month == 11:
                chamber = f"{found[1].lower()}:{seat}:g"
            elif writein or party not in PRIMARY:
                continue
            else:
                chamber = f"{found[1].lower()}:{seat}:{PRIMARY[party]}"
            if precinct := re.match(r"(\d\d-\d\d\d) (.*)", place):
                code, kind = f"{day.year}:AK:{precinct[1]}", "precinct"
                names[code] = precinct[2].strip()
            elif aggregate := re.fullmatch(r"District (\d+)\s*- (.*)", place):
                code, kind = f"AK:{day.year}:{int(aggregate[1]):02}:{KINDS[aggregate[2]]}", "aggregate"
                names[code] = f"District {int(aggregate[1])} {aggregate[2].lower()}"
            else:
                print(day, "place", place)
                continue
            rows.append((day, chamber, code, kind, "WRITE-INS" if writein else name.upper(), writein, int(votes)))
    for day, folder in SOVC.items():
        for number in range(1, 41):
            blocks: list[list] = []
            pending: list[tuple] = []
            pdf = raw.cached(SOURCE, f"{BASE}{folder}/hd{number}.pdf").path
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
                marks = [
                    w
                    for w in words
                    if w[0] < 50 and w[1] > 100 and re.fullmatch(r"\d\d-\d\d\d|District|Total|Jurisdiction", w[4])
                ]
                if not marks:
                    print(day, number, "no rows")
                    continue
                top = min(w[1] for w in marks)
                stacks: dict[float, list[tuple[float, float, float, float, str]]] = {}
                for w in words:
                    if 7.1 < w[2] - w[0] < 7.3 and w[1] > 60 and w[3] < top:
                        stacks.setdefault(round(w[0]), []).append(w)
                heads = [
                    (x, " ".join(w[4] for w in sorted(stack, key=lambda w: -w[3])))
                    for x, stack in sorted(stacks.items())
                    if len(stack) > 1 or len(stack[0][4]) > 1
                ]
                total = [w for w in marks if w[4] == "Total"]
                if not total:
                    pending.append((words, heads, marks))
                    continue
                anchors = sorted((w for w in words if abs(w[1] - total[0][1]) < 1 and w[0] > 100), key=lambda w: w[0])
                if len(heads) != len(anchors):
                    print(day, number, "columns", [h[1] for h in heads], [a[4] for a in anchors])
                    pending = []
                    continue
                table: dict[str, list[int | None]] = {}
                for page_words, page_heads, page_marks in [*pending, (words, heads, marks)]:
                    if [label for _, label in page_heads] != [label for _, label in heads]:
                        print(day, number, "continued page has other columns")
                        continue
                    edges = [a[2] + page[0] - head[0] for a, page, head in zip(anchors, page_heads, heads, strict=True)]
                    for start in sorted((w for w in page_marks if w[4] != "Total"), key=lambda w: w[1]):
                        line = sorted((w for w in page_words if abs(w[1] - start[1]) < 1), key=lambda w: w[0])
                        cells: list[int | None] = [None] * len(anchors)
                        label = []
                        for w in line:
                            column = next((i for i, edge in enumerate(edges) if abs(w[2] - edge) < 3), None)
                            if column is None or not re.fullmatch(r"[\d,.%-]+", w[4]):
                                label.append(w[4])
                            elif re.fullmatch(r"[\d,]+", w[4]):
                                cells[column] = int(w[4].replace(",", ""))
                        if label != ["Jurisdiction", "Wide"]:
                            table[" ".join(label)] = cells
                pending = []
                table["Total"] = [
                    int(a[4].replace(",", "")) if re.fullmatch(r"[\d,]+", a[4]) else None for a in anchors
                ]
                titles: list[list[float | str]] = []
                for w in sorted(
                    (w for w in words if 80 < w[1] < 140 and w[0] > 100), key=lambda w: (round(w[1]), w[0])
                ):
                    if titles and abs(w[1] - titles[-1][3]) < 2 and w[0] - titles[-1][1] < 6:
                        titles[-1][1] = w[2]
                        titles[-1][2] += f" {w[4]}"
                    else:
                        titles.append([w[0], w[2], w[4], w[1]])
                starts = [i for i, (_, label) in enumerate(heads) if label.startswith("Reg")]
                if starts and starts[0] > 0 and blocks:
                    blocks[-1][3].extend(
                        (heads[i][1], {place: values[i] for place, values in table.items()}) for i in range(starts[0])
                    )
                for k, begin in enumerate(starts):
                    end = starts[k + 1] if k + 1 < len(starts) else len(heads)
                    left, right = heads[begin][0], heads[end - 1][0] + 10
                    best = max(titles, key=lambda t: min(right, t[1]) - max(left, t[0]), default=None)
                    cut = next((i for i in range(begin, end) if heads[i][1].startswith("Total")), None)
                    found = best and re.fullmatch(r"(HOUSE|SENATE) DISTRICT (\w+)", str(best[2]).strip())
                    if cut is None or not found:
                        blocks.append([None, None, {}, []])
                        continue
                    blocks.append(
                        [
                            found[1].lower(),
                            found[2].lower() if found[1] == "SENATE" else str(int(found[2])),
                            {place: values[cut] for place, values in table.items()},
                            [
                                (heads[i][1], {place: values[i] for place, values in table.items()})
                                for i in range(cut + 1, end)
                            ],
                        ]
                    )
            for office, seat, counted, columns in blocks:
                if office is None:
                    continue
                for label, column in columns:
                    if column.get("Total") != sum(v or 0 for place, v in column.items() if place != "Total"):
                        print(day, number, office, seat, label, "column sum", column)
                for place, votes in counted.items():
                    if place != "Total" and votes != sum(column.get(place) or 0 for _, column in columns):
                        print(day, number, office, seat, place, "row sum", votes)
                for label, column in columns:
                    writein = label.lower().startswith("write")
                    candidate = "WRITE-INS" if writein else re.sub(r" \(\w+\)$", "", label).strip().upper()
                    for place, votes in column.items():
                        if place == "Total" or votes is None:
                            continue
                        if precinct := re.fullmatch(r"(\d\d-\d\d\d) (.*)", place):
                            code, kind = f"{day.year}:AK:{precinct[1]}", "precinct"
                            names[code] = precinct[2].strip()
                        elif aggregate := re.fullmatch(r"District (\d+)\s*- (.*)", place):
                            code, kind = f"AK:{day.year}:{int(aggregate[1]):02}:{KINDS[aggregate[2]]}", "aggregate"
                            names[code] = f"District {int(aggregate[1])} {aggregate[2].lower()}"
                        else:
                            print(day, number, "place", place)
                            continue
                        rows.append((day, f"{office}:{seat}:g", code, kind, candidate, writein, votes))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "code": pl.String,
            "kind": pl.String,
            "candidate": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("day", "chamber", "code", "kind", "candidate", "writein").agg(pl.col("votes").sum()).select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("AK"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind="kind",
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein="writein",
        votes="votes",
        source=pl.lit("AK"),
    ).write_parquet(PARSED / "precincts.parquet")
    pl.DataFrame(list(names.items()), schema=["code", "name"], orient="row").write_parquet(PARSED / "names.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    names = dict(pl.read_parquet(PARSED / "names.parquet").iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2006, 2008, 2010, 2012, and 2014 State House and Senate generals and 2014 legislative "
            "primaries by precinct, with absentee, question, and early votes by House district",
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
        for url in urls():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"AK": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Division of Elections' official results by precinct.",
        )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        state, parent = conn.execute(
            """select p.id, u.id from reporting_unit u join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.state' and p.iso_code = 'US-AK'"""
        ).fetchone()
        for code, kind, first, last in (
            frame.group_by("code", "kind").agg(pl.col("day").min().alias("first"), pl.col("day").max()).iter_rows()
        ):
            scheme = "us.precinct" if kind == "precinct" else "us.aggregate"
            unit = cs.unit(scheme, code)
            if str(unit) not in used or unit not in cs.minted:
                continue
            district = int(code.split(":")[2][:2])
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=state,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=cs.area("us.sldl", f"AK:{district}"),
                kind="standard" if kind == "precinct" else "aggregate",
                valid_from=first,
                valid_to=last + timedelta(days=1),
            )
            cs.add("name", entity_id=unit, text=names[code], lang="en", kind="official", is_preferred=True)
        return cs.write()
