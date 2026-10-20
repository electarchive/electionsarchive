import re
import subprocess
from datetime import date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nj_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Jersey Division of Elections"
BASE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
FILES = """1998/1998-gen-elect-reg-voter-ballots-cast.pdf 1998/1998-gen-elect-reg-voter-ballots-cast-AMENDED.pdf 1999/1999-gen-elect-reg-voters-ballots-cast.pdf
1999/1999-primary-election-turnout-by-party.pdf 2000/2000-Primary-election-turnout-by-party.pdf 2000/2000-general-election-reg-voters-ballots-cast.pdf
2000/2000-general-election-reg-voters-ballots-cast-amended.pdf 2001/2001gen-elect-voter_turnout.pdf 2001/2001pri-voter_registration-by-county.pdf
2002/2002-primary-election-voter-registration-turnout.pdf 2002/2002gen-elect-voter_turnout.pdf 2003/2003genelectionchart.pdf 2003/2003primary_regs_ballotscast_.pdf
2004/2004-Official_primary_election_turnout_04.pdf 2004/2004general_election_ballot_cast.pdf 2005/2005-general-election-chart-rev.pdf 2005/2005PrimaryTurnout7-11-05.pdf
2006/2006-general-election-chart.pdf 2006/2006_primary_regs_ballots-cast-rev..pdf 2007/2007-Gen-Election-Ballots-cast-by-county.revised.pdf
2007/2007-primary-election-turnout-chart.pdf 2008/2008-gen-election-ballots-cast-by-county-rev-120208.pdf 2008/2008_june-primary_regs_ballots-cast-070808.pdf
2009/2009-gen-election-ballots-cast-by-county-112309.pdf 2010/2010-gen-election-ballots-cast-by-county-112910.pdf
2010/2010-gen-election-ballots-cast-by-county-021711-amended-report.pdf 2010/2010-primary-election-turnout-by-party-ballots-cast-070810.pdf
2011/2011-gen-election-ballots-cast-by-county-110811.pdf 2013/2013-official-general-election-voter-turnout.pdf
2013/2013-official-special-general-us-senate-voter-turnout-0128.pdf 2013/2013-official-special-primary-us-senate-voter-turnout.pdf
2014/2014-official-general-election-voter-turnout.pdf 2014/2014-official-primary-voter-turnout.pdf 2015/2015-official-general-election-voter-turnout.pdf
2015/2015-official-primary-voter-turnout.pdf 2016/2016-official-primary-voter-turnout.pdf 2016/2016-summary-registered-voters-general-election-0131.pdf
2017/2017-official-primary-voter-turnout.pdf 2017/2017-summary-registered-voters-general-election-1201a.pdf 2018/2018-primary-election-voter-turnout.pdf
2018/2018-summary-registered-voters-general-election-1220.pdf 2019/2019-primary-election-voter-turnout.pdf 2019/2019-summary-registered-voters-general-election.pdf
2020/2020-official-general-voter-turnout.pdf 2020/2020-official-primary-voter-turnout-amended-0826.pdf 2021/2021-general-election-voter-turnout.pdf
2021/2021-primary-election-voter-turnout.pdf 2022/2022-official-general-voter-turnout.pdf 2022/2022-official-primary-voter-turnout.pdf
2023/2023-official-general-voter-turnout.pdf 2023/2023-primary-election-voter-turnout.pdf 2024/2024-official-general-voter-turnout.pdf
2024/2024-primary-election-voter-turnout.pdf 2024/2024-special-general-election-voter-turnout.pdf 2024/2024-special-primary-election-voter-turnout.pdf
2025/2025-official-general-voter-turnout.pdf 2025/2025-primary-election-voter-turnout.pdf 2026/2026-primary-election-voter-turnout.pdf
2026/2026-special-general-election-voter-turnout.pdf 2026/2026-special-primary-election-voter-turnout.pdf""".split()
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '34%'
"""
ROW = re.compile(r"^\s*([A-Za-z. ]+?)\s*\*?\s{2,}(\d[\d,]*(?:\s+[\d,%.]+)*)\s*$")
DATE = re.compile(r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})", re.I)


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for file in FILES:
        raw.fetch(SOURCE, BASE + file, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for file in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + file).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        found = DATE.search(" ".join(text[:900].split()))
        if found is None:
            print(f"{file}: no date")
            continue
        day = datetime.strptime(f"{found[1].title()} {found[2]} {found[3]}", "%B %d %Y").date()
        primary = "primary" in text[:500].lower()
        registration_only = "registered voters by party" in " ".join(text[:500].lower().split())
        counties = {}
        for line in text.splitlines():
            match = ROW.match(line)
            if match is None or plain(match[1]) not in codes:
                continue
            numbers = [int(t.replace(",", "")) for t in match[2].split() if re.fullmatch(r"[\d,]+", t)]
            code = codes[plain(match[1])]
            if registration_only:
                counties[code] = (numbers[4], None)
            elif primary:
                if len(numbers) < 4 or numbers[1] + numbers[2] > numbers[3]:
                    print(f"{file} {match[1]}: {numbers}")
                    continue
                counties[code] = (numbers[0], numbers[3])
            else:
                if len(numbers) < 2 or numbers[1] > numbers[0]:
                    print(f"{file} {match[1]}: {numbers}")
                    continue
                counties[code] = (numbers[0], numbers[1])
        print(file, day, len(counties))
        for code, (registered, voters) in counties.items():
            rows.append({"file": file, "date": day, "primary": primary, "code": code, "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows, schema_overrides={"voters": pl.Int64})
    frame = frame.with_columns(pl.col("file").str.contains("(?i)amended").alias("amended")).sort("amended", maintain_order=True)
    frame = frame.unique(["date", "primary", "code"], keep="last", maintain_order=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date", "primary").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's registered voters and ballots cast by county for the generals, specials, and primaries of 1998–2026 "
            "from the Division of Elections' turnout tables",
            polity="US",
        )
        elections: dict[date, tuple[str, str]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':NJ(:[a-z]+)?$'"""
        ):
            elections[start] = (value, entity_id)
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter turnout by county"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://www.nj.gov/state/elections/election-information-ballots-cast.shtml",
                license="Public record",
            )
        for (day, primary), rows in frame.group_by("date", "primary", maintain_order=True):
            found = elections.get(day)
            if found is None:
                near = [d for d in elections if abs((d - day).days) <= 2]
                found = elections[near[0]] if len(near) == 1 else None
            if found is None:
                print(f"{day}: no election")
                continue
            value_key, election_id = found
            cs.artifact(raw.cached(SOURCE, BASE + rows["file"][0]), source)
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
                completeness=round(rows.height / 21, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    if value is not None:
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
        return cs.write()
