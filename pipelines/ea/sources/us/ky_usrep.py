import re
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ky_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
SITE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/"
PUBLISHER = "Kentucky State Board of Elections"
TITLE = "Election results by county"
FILES = {
    date(1980, 11, 4): ("20260509191325", "1980-1989/1980/80res_usrep1.txt"),
    date(1982, 11, 2): ("20260608043720", "1980-1989/1982/82usrep9.txt"),
    date(1984, 11, 6): ("20260608045034", "1980-1989/1984/84usrep7.txt"),
    date(1986, 11, 4): ("20260814071617", "1980-1989/1986/86usrep5.txt"),
    date(1988, 11, 8): ("20250627100717", "1980-1989/1988/88usrep2.txt"),
    date(1990, 11, 6): ("20260131181953", "1990-1999/1990/90usrep.txt"),
    date(1992, 11, 3): ("20120927172040", "1990-1999/1992/92usrep.txt"),
    date(1994, 11, 8): ("20120927231933", "1990-1999/1994/94gen_USRep.txt"),
    date(1996, 11, 5): ("20120927165159", "1990-1999/1996/96Gen_usrep1.txt"),
    date(1998, 11, 3): ("20120927170946", "1990-1999/1998/98Gen_usrep.txt"),
}
SUMMARY = "Kentucky's U.S. House general elections of 1980 through 1998 by county from the State Board of Elections"
METHOD = (
    "County totals are from the State Board of Elections' fixed-width result files, read from the Internet Archive's "
    "copies. Each candidate's county sum equals the district total printed under the table."
)


def address(day: date) -> str:
    stamp, path = FILES[day]
    return WAYBACK.format(stamp, SITE + path)


def fetch() -> None:
    for day in FILES:
        try:
            raw.cached(SOURCE, address(day))
        except LookupError:
            for attempt in range(6):
                time.sleep(2 + 30 * attempt)
                try:
                    raw.fetch(SOURCE, address(day), suffix=".txt")
                    break
                except httpx.HTTPError:
                    if attempt == 5:
                        raise


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '21%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, int, str, str, str, int]] = []
    for day in FILES:
        lines = raw.cached(SOURCE, address(day)).path.read_text(errors="replace").replace("\r", "").split("\n")
        lines = [line.rstrip() for line in lines]
        dashes = [index for index, line in enumerate(lines) if re.fullmatch(r"\s*-{20,}", line)]
        start = 0
        seen: set[int] = set()
        for total_at in [index for index, line in enumerate(lines) if line.startswith("TOTAL VOTES")]:
            below = [index for index in dashes if index < total_at]
            top, bottom = below[-2], below[-1]
            header = [line for line in lines[start:top] if line.strip()]
            body = [line for line in lines[top + 1 : bottom] if line.strip()]
            start = total_at + 1
            district = int(re.search(r"(\d+)(?:ST|ND|RD|TH)\s+DIST", "\n".join(header), re.IGNORECASE)[1])
            if district in seen:
                continue
            seen.add(district)
            totals = [int(value) for value in lines[total_at][11:].split()]
            width = len(totals)
            names: list[list[str]] = [[] for _ in range(width)]
            for line in header:
                indent = len(line) - len(line.lstrip())
                if indent < 14:
                    continue
                for column in range(width):
                    piece = line[14 + 18 * column : 32 + 18 * column].strip()
                    if piece and not (
                        (indent - 14) % 18 == 2 and re.fullmatch(r"[A-Z]{2,4}", piece)
                    ):
                        names[column].append(piece)
            sums = [0] * width
            for line in body:
                code = counties[re.sub(r"[^a-z]", "", re.split(r"\t|\d", line)[0].lower()) if "\t" in line else re.sub(r"[^a-z]", "", line[:14].lower())]
                cells = [0] * width
                if "\t" in line:
                    found = [int(value) for value in re.findall(r"\d+", line[7:])]
                    if len(found) != width:
                        raise ValueError(f"{day} {district}: {line!r} does not fit {width} columns")
                    cells = found
                    line = ""
                for match in re.finditer(r"\d+", line[14:]):
                    column = (match.end() + 14 - 15) // 18
                    if column >= width:
                        if not int(match[0]):
                            continue
                        raise ValueError(f"{day} {district}: {line!r} runs past {width} columns")
                    if cells[column]:
                        raise ValueError(f"{day} {district}: {line!r} does not fit {width} columns")
                    cells[column] = int(match[0])
                for column, value in enumerate(cells):
                    sums[column] += value
                    rows.append((str(day), district, code, " ".join(names[column]), str(column), value))
            if sums != totals:
                raise ValueError(f"{day} district {district}: columns sum to {sums}, printed {totals}")
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "label", "column", "votes"], orient="row")
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
                url="https://elect.ky.gov/results/",
                license="Public record",
            )
        for day in FILES:
            cs.artifact(raw.cached(SOURCE, address(day)), source)
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '21%%'"
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                "select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '21%%'"
            ).fetchall()
        )
        split = {
            (day, county)
            for (day, county), districts in frame.group_by("day", "county")
            if districts["district"].n_unique() > 1
        }
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':KY:house:'")
        )
        for (day, district), rows in frame.group_by("day", "district", maintain_order=True):
            key = f"{day}:KY:house:{district}"
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
            roots = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and parent_option_id is null "
                "and kind = 'candidate'",
                [contest],
            ).fetchall()
            targets: dict[str, object] = {}
            for column, label in rows.select("column", "label").unique(maintain_order=True).iter_rows():
                words = [re.sub(r"[^a-z]", "", word.lower()) for word in label.split()]
                for attempt in ([[word] for word in words[-1:] if len(word) >= 3], [[word] for word in words if len(word) >= 3]):
                    found = {
                        option
                        for guess in attempt
                        for option, ballot in roots
                        if guess[0] and guess[0] in re.sub(r"[^a-z]", "", (ballot or "").lower())
                    }
                    if len(found) == 1:
                        targets[column] = found.pop()
                        break
            if len(set(targets.values())) != len(targets):
                print(f"{key}: two columns match one option")
                continue
            sums = {column: int(g["votes"].sum()) for (column,), g in rows.group_by("column")}
            missing = sorted(set(rows["column"]) - set(targets))
            if any(sums[column] * 100 > sum(sums.values()) for column in missing):
                print(f"{key}: unmatched {[(c, sums[c]) for c in missing]}; options {[b for _, b in roots]}")
                continue
            if missing:
                writeins = cs.key("us.option", f"{key}:writein:ky_usrep")
                if writeins in cs.minted:
                    cs.add("contest_option", id=writeins, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                for column in missing:
                    targets[column] = writeins
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
                is_default=True,
                supersedes_id=row[0],
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
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
                        polity_id=cs.polity("US-KY"),
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
