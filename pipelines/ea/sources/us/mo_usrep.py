import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import mo_sos
from ea.store import DATA, connect

SOURCE = "us.mo_usrep"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
TITLE = "Official election returns"
SITE = mo_sos.SITE
FILES = {
    1996: (mo_sos.COUNTY, mo_sos.STATE, mo_sos.SOURCE),
}
DAYS = {1996: date(1996, 11, 5)}
SUMMARY = "Missouri's U.S. House general election of 1996 by county from the Secretary of State's official returns"
METHOD = (
    "County totals are from the Secretary of State's county reporting files, read from the Internet Archive's copies. "
    "Each candidate's county sum equals the statewide total in the Secretary of State's all-races file. Kansas City, which "
    "reports apart, is added into Jackson County."
)
ROW = re.compile(r"\s+(\S.*?)\s{2,}([A-Z]{2,3})\s+([\d,]+)(?:\s+[\d.]+%)?")


def fetch() -> None:
    for county, state, _ in FILES.values():
        raw.fetch(mo_sos.SOURCE, county, suffix=".pdf")
        raw.fetch(mo_sos.SOURCE, state, suffix=".pdf")


def read(source: str, url: str) -> dict[tuple[str, int], list[tuple[str, str, int]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(source, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    found: dict[tuple[str, int], list[tuple[str, str, int]]] = {}
    county = "STATE"
    district: int | None = None
    for line in text.splitlines():
        if heading := re.fullmatch(r" {5}([A-Z][A-Z .']+)", line):
            county, district = heading[1].strip(), None
        elif header := re.match(r"\s*U\.S\. Representative\W+District (\d+)", line):
            district = int(header[1])
            found.setdefault((county, district), [])
        elif (
            re.match(r"\s{0,5}\S", line)
            and not re.match(r"\s*(http|\d+/\d+/\d{4}|\x0c)", line)
            and not ROW.fullmatch(line)
            and "Total Votes" not in line
        ):
            district = None
        elif district is not None and (row := ROW.fullmatch(line)):
            found[(county, district)].append((row[1], row[2], int(row[3].replace(",", ""))))
        elif district is not None and (row := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)):
            if sum(votes for _, _, votes in found[(county, district)]) != int(row[1].replace(",", "")):
                raise ValueError(f"{county} district {district}: rows do not sum to {row[1]}")
            district = None
    return found


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%%'"""
            ).fetchall()
        }
    keys = {"stlouiscity": "29510", "stlouiscounty": "29189", "kansascity": codes["jackson"]}
    rows: list[dict[str, object]] = []
    for year, (county_url, state_url, source) in FILES.items():
        state = read(source, state_url)
        sums: dict[tuple[int, str], int] = {}
        for (county, district), lines in read(source, county_url).items():
            code = keys.get(re.sub(r"[^a-z]", "", county.lower())) or codes[re.sub(r"[^a-z]", "", county.lower())]
            for label, party, votes in lines:
                sums[(district, label)] = sums.get((district, label), 0) + votes
                rows.append(
                    {"year": year, "district": district, "county": code, "label": label, "party": party, "votes": votes}
                )
        printed = {(district, label): votes for (_, district), lines in state.items() for label, _, votes in lines}
        if sums != printed:
            raise ValueError(
                f"{year}: counties differ from the statewide file: {sorted(set(sums.items()) ^ set(printed.items()))}"
            )
    frame = (
        pl.DataFrame(rows)
        .group_by("year", "district", "county", "label", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "district")
        .agg(pl.col("county").n_unique(), pl.col("label").n_unique())
        .sort("year", "district")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US")
        source = cs.source(PUBLISHER, TITLE)
        for county_url, state_url, origin in FILES.values():
            cs.artifact(raw.cached(origin, county_url), source)
            cs.artifact(raw.cached(origin, state_url), source)
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '29%%'"
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                "select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '29%%'"
            ).fetchall()
        )
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MO:house:'"
            )
        )
        for (year, district), rows in frame.group_by("year", "district", maintain_order=True):
            day = DAYS[year]
            key = f"{day}:MO:house:{district}"
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
                   join reporting_unit u on u.k = v.unit_k where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
                [old[0][0]],
            ).fetchall()
            if county_rows:
                continue
            row = old[0]
            roots = conn.execute(
                """select o.id, o.ballot_label, o.kind, v.value from contest_option o
                   join result_value v on v.option_k = o.k join result_set r on r.k = v.result_set_k
                   where o.contest_id = %s and o.parent_option_id is null and r.id = %s""",
                [contest, row[0]],
            ).fetchall()
            writein = [option for option, _, kind, _ in roots if kind == "write_in"]
            rows = rows.with_columns(
                pl.when(pl.col("party") == "WI").then(pl.lit("write-ins")).otherwise(pl.col("label")).alias("label")
            )
            rows = rows.group_by("county", "label", maintain_order=True).agg(pl.col("votes").sum())
            totals = {label: int(g["votes"].sum()) for (label,), g in rows.group_by("label")}
            targets: dict[str, object] = {}
            for label, total in totals.items():
                found = [
                    option for option, _, _, value in roots if int(value) == total and option not in targets.values()
                ]
                if len(found) == 1:
                    targets[label] = found[0]
                elif label == "write-ins" and len(writein) == 1:
                    targets[label] = writein[0]
            if len(targets) != len(totals) or {t for t in targets.values()} != {option for option, _, _, _ in roots} - (
                {writein[0]} if writein and "write-ins" not in totals else set()
            ):
                print(f"{key}: {totals} against FEC {[(label, kind, int(value)) for _, label, kind, value in roots]}")
                continue
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
            ((district_area,),) = conn.execute(
                "select district_area_id from contest where id = %s", [contest]
            ).fetchall()
            unit_ids = dict(units)
            for code in rows["county"].unique():
                if frame.filter((pl.col("year") == year) & (pl.col("county") == code))["district"].n_unique() < 2:
                    continue
                part = f"{year}:{code}:{district}"
                unit_ids[code] = cs.unit("us.county_part", part)
                if unit_ids[code] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[code],
                        polity_id=cs.polity("US-MO"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=units[code],
                        area_relation="within",
                        area_id=areas[code],
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=unit_ids[code],
                        area_id=district_area,
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            for code, label, votes in rows.select("county", "label", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_ids[code],
                    option_id=targets[label],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
