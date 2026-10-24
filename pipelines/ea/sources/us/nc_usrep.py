import difflib
import json
import re
import subprocess
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nc_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WORK = DATA / "work" / "nc_usrep"
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Abstract of votes cast in the general election"
BUCKET = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{d}/results_by_contest_{c}.zip"
DAYS = [date(1992, 11, 3), date(1996, 11, 5), date(1998, 11, 3)]
RECORD = "https://digital.ncdcr.gov/Documents/Detail/4199407"

SUMMARY = "North Carolina's U.S. House general elections of 1996 and 1998 by county from the State Board of Elections"
METHOD = (
    "County totals are from the State Board of Elections' abstracts of votes cast, one table per congressional "
    "district. Each candidate's county sum equals the district total printed in the abstract."
)

SCAN = (
    " The 1992 abstract is a typed scan read by two OCR passes, with hand-read cells settled against the printed column totals."
)
SCAN10 = (
    " District 10's county rows fall short of its printed total by 34 Republican and 2 Libertarian votes."
)


def address(day: date) -> str:
    if day.year == 1992:
        return RECORD
    return BUCKET.format(d=day.strftime("%Y_%m_%d"), c=day.strftime("%Y%m%d"))


def fetch() -> None:
    detail = subprocess.run(
        ["curl", "-sL", "-m", "60", "-A", "Mozilla/5.0", RECORD], capture_output=True, text=True, check=True
    ).stdout
    for item in json.loads(re.search(r"var viewerItems = (\[.*?\]);\n", detail, re.S)[1]):
        page = f"{RECORD}#page-{int(item['path'].rsplit('-', 1)[1]):02d}"
        pdf = subprocess.run(
            ["curl", "-sL", "-m", "120", "-A", "Mozilla/5.0", item["pdfUrl"].replace("[IMAGETOKEN]", item["imageToken"])],
            capture_output=True,
            check=True,
        ).stdout
        raw.store(SOURCE, page, pdf, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")
    for day in DAYS[1:]:
        try:
            raw.cached(SOURCE, address(day))
        except LookupError:
            raw.fetch(SOURCE, address(day), suffix=".zip")


def pages(day: date) -> dict[int, list[str]]:
    WORK.mkdir(parents=True, exist_ok=True)
    out: dict[int, list[str]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, address(day)).path) as archive:
        names = [name for name in archive.namelist() if "US_House" in name]
        for name in names:
            target = WORK / name
            target.write_bytes(archive.read(name))
            text = subprocess.run(["pdftotext", "-layout", str(target), "-"], capture_output=True, text=True, check=True).stdout
            number = re.search(r"US_House_(\d+)_", name)
            lines = text.replace("\f", "\n").split("\n")
            if number:
                out[int(number[1])] = lines
            else:
                out[0] = lines
    return out


def numbers(text: str) -> list[tuple[int, int]]:
    return [(match.end(), int(match[0].replace(",", ""))) for match in re.finditer(r"\d[\d,]*", text)]


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, int, str, str, int]] = []
    for line in (Path(__file__).with_name("nc_usrep_1992.tsv")).read_text().splitlines():
        district, name, column, votes = line.split("\t")
        key = re.sub(r"[^a-z]", "", name.lower())
        key = key if key in counties else difflib.get_close_matches(key, counties, 1, 0.8)[0]
        rows.append(("1992-11-03", int(district), counties[key], column, int(votes)))
    for day in DAYS[1:]:
        found = pages(day)
        tables: list[tuple[int, list[tuple[str, list[tuple[int, int]]]], list[tuple[int, int]]]] = []
        if day.year == 1996:
            lines = found[0]
            starts = [
                (index, int(re.search(r"(\d+)(?:st|nd|rd|th) Congressional District", line)[1]))
                for index, line in enumerate(lines)
                if re.search(r"\d+(?:st|nd|rd|th) Congressional District", line)
            ]
            for position, (index, district) in enumerate(starts):
                end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
                body: list[tuple[str, list[tuple[int, int]]]] = []
                total: list[tuple[int, int]] = []
                for line in lines[index + 1 : end]:
                    if re.match(r"\s+Total\s", line):
                        total = numbers(line.split("Total", 1)[1])
                        offset = line.index("Total") + 5
                        total = [(edge + offset, value) for edge, value in total]
                    elif re.match(r"\s[A-Z][A-Z ]+\s+[\d,]+", line):
                        match = re.match(r"\s([A-Z][A-Z .']+?)\s{2,}(.*)", line)
                        body.append((match[1], [(edge + match.start(2), value) for edge, value in numbers(match[2])]))
                tables.append((district, body, total))
        else:
            for district, lines in found.items():
                body = []
                total = []
                county = None
                for line in lines:
                    if re.match(r"GRAND TOTALS:", line):
                        total = [(edge + 14, value) for edge, value in numbers(line[14:])]
                    elif re.match(r"[A-Za-z .'-]+ TOTALS:", line):
                        name = line.split(" TOTALS:")[0].strip()
                        body.append((name, [(edge + 14 + len(name), value) for edge, value in numbers(line.split("TOTALS:", 1)[1])]))
                tables.append((district, body, total))
        for district, body, total in tables:
            edges = [edge for edge, _ in total]
            sums = [0] * len(total)
            for name, cells in body:
                key = re.sub(r"[^a-z]", "", name.lower())
                if key not in counties:
                    raise ValueError(f"{day} {district}: unknown county {name!r}")
                row = [0] * len(total)
                if day.year == 1996:
                    while len(cells) > len(total) and cells[-1][1] == 0:
                        cells = cells[:-1]
                if day.year == 1996 and len(cells) < len(total):
                    for edge, value in cells:
                        column = min(range(len(edges)), key=lambda c: abs(edges[c] - edge))
                        row[column] = value
                else:
                    if len(cells) != len(total):
                        raise ValueError(f"{day} {district}: {name!r} has {len(cells)} cells, expected {len(total)}")
                    row = [value for _, value in cells]
                for column, value in enumerate(row):
                    sums[column] += value
                    rows.append((str(day), district, counties[key], str(column), value))
            if sums != [value for _, value in total]:
                raise ValueError(f"{day} district {district}: columns sum to {sums}, printed {[v for _, v in total]}")
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "district").agg(pl.col("votes").sum()).sort("day", "district"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US")
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
                license="Public record",
            )
        for day in DAYS[1:]:
            cs.artifact(raw.cached(SOURCE, address(day)), source)
        for page in range(1, 7):
            cs.artifact(raw.cached(SOURCE, f"{RECORD}#page-{page:02d}"), source)
        units = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.county' and code like '37%%'").fetchall()
        )
        areas = dict(
            conn.execute("select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '37%%'").fetchall()
        )
        split = {
            (day, county)
            for (day, county), districts in frame.group_by("day", "county")
            if districts["district"].n_unique() > 1
        }
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':NC:house:'")
        )
        for (day, district), rows in frame.group_by("day", "district", maintain_order=True):
            key = f"{day}:NC:house:{district}"
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest in the archive")
                continue
            old = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            if len(old) != 1:
                print(f"{key}: {len(old)} default result sets")
                continue
            ((county_rows,),) = conn.execute(
                """select count(*) from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k
                   where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
                [old[0][0]],
            ).fetchall()
            if county_rows:
                continue
            row = old[0]
            held = dict(
                conn.execute(
                    """select o.id, sum(v.value) from result_value v join result_set r on r.k = v.result_set_k
                       join contest_option o on o.k = v.option_k
                       where r.id = %s and v.measure = 1 group by o.id""",
                    [row[0]],
                ).fetchall()
            )
            sums = {column: int(g["votes"].sum()) for (column,), g in rows.group_by("column")}
            targets: dict[str, object] = {}
            for column, total in sums.items():
                same = [option for option, value in held.items() if value == total and option not in targets.values()]
                if len(same) == 1:
                    targets[column] = same[0]
            for column, total in sums.items():
                if column in targets:
                    continue
                near = sorted(
                    (abs(value - total), option) for option, value in held.items() if option not in targets.values()
                )
                if near and near[0][0] * 50 <= total:
                    targets[column] = near[0][1]
            missing = sorted(set(sums) - set(targets))
            if any(sums[column] * 100 > sum(sums.values()) for column in missing):
                print(f"{key}: unmatched {[(c, sums[c]) for c in missing]}; held {held}")
                continue
            if missing:
                writeins = cs.key("us.option", f"{key}:writein:nc_usrep")
                if writeins in cs.minted:
                    cs.add("contest_option", id=writeins, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                for column in missing:
                    targets[column] = writeins
            totals = {}
            for column, option in targets.items():
                totals[option] = totals.get(option, 0) + sums[column]
            ties = all(held.get(option) == value for option, value in totals.items()) and set(held) <= set(totals)
            differences = [
                f"{int(value - held.get(option, 0)):+d}" for option, value in totals.items() if held.get(option, 0) != value
            ]
            print(key, "ties" if ties else f"differs {differences}")
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if ties:
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            cs.add(
                "result_set",
                id=result_set,
                election_id=row[1],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=ties,
                supersedes_id=row[0] if ties else None,
                completeness=1,
            )
            note = METHOD + (SCAN if day.startswith("1992") else "") + (SCAN10 if key.endswith("1992-11-03:NC:house:10") else "")
            note = note if ties else note + f" It differs from the default result set by {', '.join(differences)} votes."
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            if ties:
                for option, kind, seats in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [row[0]]
                ).fetchall():
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            unit_ids = dict(units)
            ((district_area,),) = conn.execute("select district_area_id from contest where id = %s", [contest]).fetchall()
            for county in rows["county"].unique():
                if (day, county) not in split:
                    continue
                part = f"{day[:4]}:{county}:{district}"
                unit_ids[county] = cs.unit("us.county_part", part)
                if unit_ids[county] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[county],
                        polity_id=cs.polity("US-NC"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=units[county],
                        area_relation="within",
                        area_id=areas[county],
                        kind="standard",
                        valid_from=date.fromisoformat(day),
                        valid_to=date.fromisoformat(day) + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=unit_ids[county],
                        area_id=district_area,
                        valid_from=date.fromisoformat(day),
                        valid_to=date.fromisoformat(day) + timedelta(days=1),
                    )
            merged: dict[tuple[str, object], int] = {}
            for county, column, votes in rows.select("county", "column", "votes").iter_rows():
                merged[(county, targets[column])] = merged.get((county, targets[column]), 0) + votes
            for (county, option), votes in merged.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_ids[county],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
