import html
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl
from ea.sources.us.ak_leg import KINDS
from ea.store import DATA, connect

SOURCE = "us.ak_sovc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual"
FOLDERS = {2000: ("MF-359", "2000-11-07"), 2002: ("MF-266", "2002"), 2006: ("MF-266", "2006"), 2010: ("MF-266", "2010")}
BASE = "https://www.elections.alaska.gov/results/"
DAYS = {2000: date(2000, 11, 7), 2002: date(2002, 11, 5), 2006: date(2006, 11, 7), 2010: date(2010, 11, 2)}
PUBLISHER = "Alaska Division of Elections"
TITLE = "Statements of votes cast by House district and precinct results"
SOVC = {2000, 2002}
OFFICES = {"US SENAT": "senate", "US REPRESENTATIVE": "house", "GOVERNOR": "governor"}


def files() -> list[tuple[int, int, Path]]:
    found = []
    for year in DAYS:
        for number in range(1, 41):
            names = [f"sovc{number}.pdf"] if year in SOVC else [f"hd{number}.pdf", f"hd{number} (1).pdf"]
            folder = INBOX / "/".join(FOLDERS[year])
            found.append((year, number, next(folder / name for name in names if (folder / name).exists())))
    return found


def read(
    path: Path, year: int
) -> list[tuple[str | None, dict[str, int | None], list[tuple[str, dict[str, int | None]]]]]:
    blocks: list[list] = []
    pending: list[tuple] = []
    markup = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
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
            if w[1] > 100
            and (
                (w[0] < 50 and re.fullmatch(r"\d\d-\d\d\d|District|Total|Jurisdiction", w[4]))
                or (year in SOVC and (64 < w[0] < 66 or (w[0] < 50 and w[4] == "State")))
                or (year == 2000 and w[0] < 60 and w[4] == "SubTotal")
            )
        ]
        if not marks:
            print(path.name, "no rows")
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
        total = [w for w in marks if w[4] in {"Total", "SubTotal"}]
        if not total:
            pending.append((words, heads, marks))
            continue
        anchors = sorted((w for w in words if abs(w[1] - total[0][1]) < 1 and w[0] > 100), key=lambda w: w[0])
        if len(heads) != len(anchors):
            print(path.name, "columns", [h[1] for h in heads], [a[4] for a in anchors])
            pending = []
            continue
        table: dict[str, list[int | None]] = {}
        for page_words, page_heads, page_marks in [*pending, (words, heads, marks)]:
            if [label for _, label in page_heads] != [label for _, label in heads]:
                print(path.name, "continued page has other columns")
                continue
            edges = [a[2] + page[0] - head[0] for a, page, head in zip(anchors, page_heads, heads, strict=True)]
            for start in sorted(
                (w for w in page_marks if w[4] not in {"Total", "SubTotal", "State"}), key=lambda w: w[1]
            ):
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
        table["Total"] = [int(a[4].replace(",", "")) if re.fullmatch(r"[\d,]+", a[4]) else None for a in anchors]
        titles: list[list[float | str]] = []
        banner = max((w[1] for w in words if w[1] < 140 and w[4].lower() in {"results", "(recount)"}), default=0)
        for w in sorted(
            (w for w in words if banner + 4 < w[1] < 140 and w[0] > 100), key=lambda w: (round(w[1]), w[0])
        ):
            if titles and abs(w[1] - titles[-1][3]) < 2 and w[0] - titles[-1][1] < 6:
                titles[-1][1] = w[2]
                titles[-1][2] += f" {w[4]}"
            else:
                titles.append([w[0], w[2], w[4], w[1]])
        starts = [i for i, (_, label) in enumerate(heads) if label.startswith("Total" if year == 2000 else "Reg")]
        if starts and starts[0] > 0 and blocks:
            blocks[-1][3].extend(
                (heads[i][1], {place: values[i] for place, values in table.items()}) for i in range(starts[0])
            )
        for k, begin in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(heads)
            left, right = heads[begin][0], heads[end - 1][0] + 10
            best = max(titles, key=lambda t: min(right, t[1]) - max(left, t[0]), default=None)
            cut = next((i for i in range(begin, end) if heads[i][1].startswith("Total")), None)
            if cut is None or best is None:
                blocks.append([None, None, {}, []])
                continue
            blocks.append(
                [
                    str(best[2]).strip(),
                    None,
                    {place: values[cut] for place, values in table.items()},
                    [(heads[i][1], {place: values[i] for place, values in table.items()}) for i in range(cut + 1, end)],
                ]
            )
    return [(b[0], b[2], b[3]) for b in blocks]


def fetch() -> None:
    for year, number, path in files():
        raw.store(
            SOURCE,
            f"manual://{FOLDERS[year][0]}/{year}/{path.name}",
            path.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 30, tzinfo=UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    rows: list[tuple] = []
    names: dict[str, str] = {}
    for year, number, path in files():
        day = DAYS[year]
        for title, counted, columns in read(
            raw.cached(SOURCE, f"manual://{FOLDERS[year][0]}/{year}/{path.name}").path, year
        ):
            office = next((office for prefix, office in OFFICES.items() if (title or "").startswith(prefix)), None)
            seat = re.fullmatch(r"(HOUSE|SENATE) DISTRICT (\w+)", title or "")
            if office is None and year in SOVC and seat:
                office = f"{seat[1].lower()}:{int(seat[2]) if seat[1] == 'HOUSE' else seat[2].lower()}:g"
            if office is None or (year == 2000 and ":" not in office):
                continue
            for label, column in columns:
                if column.get("Total") != sum(v or 0 for place, v in column.items() if place != "Total"):
                    print(year, number, office, label, "column sum", column.get("Total"))
            for place, votes in counted.items():
                if place != "Total" and votes != sum(column.get(place) or 0 for _, column in columns):
                    print(year, number, office, place, "row sum", votes)
            for label, column in columns:
                writein = label.lower().startswith("write")
                candidate = "WRITE-INS" if writein else re.sub(r" \(\w+\)$", "", label).split("/")[0].strip().upper()
                for place, votes in column.items():
                    if votes is None:
                        continue
                    if place == "Total":
                        code, kind = f"{day.isoformat()}:{number:02}", "district"
                    elif precinct := re.fullmatch(r"(\d\d-\d\d\d) (.*)", place):
                        code, kind = f"{year}:AK:{precinct[1]}", "precinct"
                        names[code] = precinct[2].strip()
                    elif aggregate := re.fullmatch(
                        r"District (\d+)\s*- (.*)|District (\d+) (Absentee|Question|Early Voting)", place
                    ):
                        label_kind = aggregate[2] or aggregate[4]
                        code, kind = f"AK:{year}:{number:02}:{KINDS[label_kind]}", "aggregate"
                        names[code] = f"District {number} {label_kind.lower()}"
                    elif year in SOVC:
                        code, kind = f"{year}:AK:{number:02}:{slug(place)}", "precinct"
                        names[code] = place
                    else:
                        print(year, number, "place", place)
                        continue
                    rows.append((year, office, day, code, kind, candidate, writein, votes))
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "chamber": pl.String,
                "day": pl.Date,
                "code": pl.String,
                "kind": pl.String,
                "candidate": pl.String,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("year", "chamber", "day", "code", "kind", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .select(
            "year",
            "chamber",
            "day",
            special=pl.lit(False),
            state=pl.lit("AK"),
            district=pl.when(pl.col("chamber") == "house").then(0).otherwise(None).cast(pl.Int16),
            code="code",
            kind="kind",
            candidate="candidate",
            measure=pl.lit(1, pl.Int16),
            writein="writein",
            votes="votes",
            source=pl.lit("AK"),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "rows.parquet")
    pl.DataFrame(list(names.items()), schema=["code", "name"], orient="row").write_parquet(PARSED / "names.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    names = dict(pl.read_parquet(PARSED / "names.parquet").iter_rows())
    missing = (
        (pl.col("year") == 2002)
        | ((pl.col("year") == 2006) & (pl.col("chamber") == "governor"))
        | ((pl.col("year") == 2010) & ~pl.col("chamber").is_in(["senate"]))
    ) & ~pl.col("chamber").str.contains(":")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's 2002, 2006, and 2010 general elections for governor, U.S. Senate, and U.S. House, and 2000 and 2002 "
            "legislative races, by precinct and House district, from the Division of Elections' statements of votes cast",
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
        for year, number, path in files():
            cs.artifact(raw.cached(SOURCE, f"manual://{FOLDERS[year][0]}/{year}/{path.name}"), source)
        state, parent = conn.execute(
            """select p.id, u.id from reporting_unit u join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.state' and p.iso_code = 'US-AK'"""
        ).fetchone()
        districts = frame.filter((pl.col("kind") == "district") & missing)
        for code, day in districts.select("code", "day").unique().iter_rows():
            unit = cs.unit("us.ak_house_district", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
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
        medsl.attach(cs, conn, districts, {"AK": source}, strict=True)
        places = frame.filter(pl.col("kind") != "district")
        medsl.attach(
            cs,
            conn,
            places,
            {"AK": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Division of Elections' official results by precinct.",
        )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        for code, kind, first, last in (
            places.group_by("code", "kind").agg(pl.col("day").min().alias("first"), pl.col("day").max()).iter_rows()
        ):
            scheme = "us.precinct" if kind == "precinct" else "us.aggregate"
            unit = cs.unit(scheme, code)
            if str(unit) not in used or unit not in cs.minted:
                continue
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=state,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                area_relation="within",
                area_id=cs.area("us.sldl", f"AK:{int(code.split(':')[2][:2])}"),
                kind="standard" if kind == "precinct" else "aggregate",
                valid_from=first,
                valid_to=last + timedelta(days=1),
            )
            cs.add("name", entity_id=unit, text=names[code], lang="en", kind="official", is_preferred=True)
        return cs.write()


if __name__ == "__main__":
    import sys

    for year, number, path in files():
        if len(sys.argv) > 1 and str(year) != sys.argv[1]:
            continue
        for title, counted, columns in read(path, year):
            print(year, number, title, len(counted), [label for label, _ in columns])
