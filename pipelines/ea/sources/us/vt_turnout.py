import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.vt_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Vermont Secretary of State"
BASE = "https://outside.vermont.gov/dept/sos/Elections_Division/election_info_resources/elections_results_data/"
REGISTRATION = BASE + "1974-2024_voter_registration_totals.xlsx"
REPORTS = [(2018, "general"), (2020, "general"), (2022, "general"), (2024, "general"), (2020, "primary"), (2022, "primary"), (2024, "primary")]
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '50%'
"""
NUMBERS = r"([\d,]+)\s+([\d,]+)\s+[\d.]+%"


def url(year: int, kind: str) -> str:
    return f"{BASE}{year}_{kind}_election_voter_turnout.pdf"


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i)( county)$", "", name.strip()).lower())


def fetch() -> None:
    for address, suffix, media in [(url(y, k), ".pdf", "application/pdf") for y, k in REPORTS] + [(REGISTRATION, ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]:
        try:
            raw.cached(SOURCE, address)
            continue
        except LookupError:
            pass
        response = httpx.get(address, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
        response.raise_for_status()
        raw.store(SOURCE, address, response.content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for year, kind in REPORTS:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year, kind)).path), "-"], capture_output=True, text=True, check=True
        ).stdout.replace("\x0c", "")
        day = re.search(r"Election:.*?(\d\d)/(\d\d)/(\d{4})", text)
        election_day = date(int(day[3]), int(day[1]), int(day[2]))
        county = None
        party = None
        sums: dict[tuple[str | None, str], list[int]] = {}
        totals: dict[tuple[str | None, str], tuple[int, int]] = {}
        for line in text.split("\n"):
            line = line.rstrip()
            found = re.search(r"Party: (\w+)", line)
            if found:
                party = found[1]
                continue
            if re.fullmatch(r"[A-Z][A-Z ]*", line) and plain(line) in codes:
                county = plain(line)
                continue
            total = re.match(rf"^\s*([A-Z][A-Z ]+?)(?: Total)?\s{{2,}}{NUMBERS}", line)
            if total and plain(total[1]) in codes and not re.match(rf"^[A-Z ]+\s{{2,}}\S.*?\s{{2,}}{NUMBERS}", line):
                totals[(party, plain(total[1]))] = (int(total[2].replace(",", "")), int(total[3].replace(",", "")))
                continue
            town = re.match(rf"^([A-Z][A-Z .'-]*?)\s{{2,}}(\S.*?)\s{{2,}}{NUMBERS}", line)
            if town and county:
                entry = sums.setdefault((party, county), [0, 0])
                entry[0] += int(town[3].replace(",", ""))
                entry[1] += int(town[4].replace(",", ""))
        for key, entry in sums.items():
            if totals.get(key) != tuple(entry):
                raise ValueError(f"{year} {kind} {key}: towns sum to {entry}, printed {totals.get(key)}")
        by_county: dict[str, list[int]] = {}
        for (_, name), entry in sums.items():
            current = by_county.setdefault(name, [entry[0], 0])
            current[1] += entry[1]
        for name, (registered, voters) in by_county.items():
            if kind == "primary":
                registered = max(e[0] for (p, n), e in sums.items() if n == name)
            rows.append({"date": election_day, "code": codes[name], "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows)
    sheet = pl.read_excel(raw.cached(SOURCE, REGISTRATION).path, sheet_id=2, has_header=False)
    state = (
        sheet.select(pl.col("column_0").cast(pl.Int64, strict=False).alias("year"), pl.col("column_1").cast(pl.Int64, strict=False).alias("registered"), pl.col("column_2").cast(pl.Int64, strict=False).alias("voters"))
        .drop_nulls()
        .filter(pl.col("year") % 2 == 0)
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    state.write_parquet(PARSED / "state.parquet")
    print(frame.group_by("date").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))
    print(state.height, state["year"].min(), state["year"].max())


def emit() -> Path:
    counties = pl.read_parquet(PARSED / "counties.parquet")
    state = pl.read_parquet(PARSED / "state.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Vermont's registered voters and ballots cast by county for the generals of 2018–2024 and the primaries of 2020–2024, "
            "summed from the Secretary of State's town turnout reports, and statewide for the generals of 1974–2024 from the canvassing committee's totals",
            polity="US",
        )
        elections = {
            start: (value, entity_id, kind)
            for value, entity_id, start, kind in conn.execute(
                """select x.value, x.entity_id, e.date_from, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':VT(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Turnout"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://sos.vermont.gov/elections/election-info-resources/elections-results-data/",
                license="Public record",
            )
        for year, kind in REPORTS:
            cs.artifact(raw.cached(SOURCE, url(year, kind)), source)
        cs.artifact(raw.cached(SOURCE, REGISTRATION), source)
        generals = {start.year: start for start, (_, _, kind) in elections.items() if kind == "general"}
        for day, rows in counties.group_by("date", maintain_order=True):
            day = day[0]
            if day not in elections:
                print(f"{day}: no election")
                continue
            value_key, election_id, _ = elections[day]
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / 14, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        covered = {d.year for d in counties["date"].unique().to_list() if elections.get(d, (0, 0, ""))[2] == "general"}
        for year, registered, voters in state.select("year", "registered", "voters").iter_rows():
            if year in covered or year not in generals:
                continue
            value_key, election_id, _ = elections[generals[year]]
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:state")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=1,
            )
            for measure, value in ((2, registered), (3, voters)):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", "50"),
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
