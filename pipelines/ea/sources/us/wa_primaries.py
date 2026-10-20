import csv
import io
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wa_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://results.vote.wa.gov/results/{day:%Y%m%d}/"
DAYS = [date(2008, 8, 19), date(2010, 8, 17), date(2012, 8, 7)]
ALL_COUNTIES = "export/20120807_AllCounties_20120904_1126.csv"
COUNTIES = [
    "Adams", "Asotin", "Benton", "Chelan", "Clallam", "Clark", "Columbia", "Cowlitz", "Douglas", "Ferry",
    "Franklin", "Garfield", "Grant", "GraysHarbor", "Island", "Jefferson", "King", "Kitsap", "Kittitas",
    "Klickitat", "Lewis", "Lincoln", "Mason", "Okanogan", "Pacific", "PendOreille", "Pierce", "SanJuan",
    "Skagit", "Skamania", "Snohomish", "Spokane", "Stevens", "Thurston", "Wahkiakum", "WallaWalla", "Whatcom",
    "Whitman", "Yakima",
]  # fmt: skip
RACE = re.compile(r"(?:U\.S\. )?Congressional District (\d+)( One Month Short Term)?(?: -)? (?:U\.S\. )?Representative|(United States U\.S\. Senator)")
PUBLISHER = "Washington Secretary of State"
TITLE = "Primary election results"


def urls(day: date) -> list[str]:
    base = BASE.format(day=day)
    if day.year == 2012:
        return [base + ALL_COUNTIES]
    return [f"{base}export/{day:%Y%m%d}_{county}.csv" for county in COUNTIES]


def fetch() -> None:
    for day in DAYS:
        for url in [*urls(day), BASE.format(day=day) + "Turnout.html"]:
            raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse() -> None:
    with connect() as conn:
        fips = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
        known: dict[str, list[int]] = {}
        for contest, total in conn.execute(
            """select x.value, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^(2008-08-19|2010-08-17|2012-08-07):WA:' group by x.value, o.id"""
        ).fetchall():
            known.setdefault(contest, []).append(total)
    rows: list[tuple[date, str, int | None, bool, str, str, int]] = []
    turnout: list[tuple[date, str, int, int]] = []
    for day in DAYS:
        for url in urls(day):
            text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig")
            county_file = Path(url).stem.split("_")[1]
            for line in csv.DictReader(io.StringIO(text)):
                found = RACE.fullmatch(line["Race"])
                if found is None:
                    continue
                county = line.get("County") or county_file
                rows.append(
                    (
                        day,
                        "senate" if found[3] else "house",
                        int(found[1]) if found[1] else None,
                        bool(found[2]),
                        line["Candidate"],
                        fips[key(county)],
                        int(line["Votes"] or 0),
                    )
                )
        page = raw.cached(SOURCE, BASE.format(day=day) + "Turnout.html").path.read_text(encoding="utf-8", errors="replace")
        for county, registered, ballots in re.findall(
            r"<td[^>]*>\s*(?:<a[^>]*>)?\s*([A-Z][A-Za-z ]+?)\s*(?:</a>)?\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>",
            page,
        ):
            if key(county) in fips:
                turnout.append((day, fips[key(county)], int(registered.replace(",", "")), int(ballots.replace(",", ""))))
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "special": pl.Boolean,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("day", "chamber", "district", "special", "candidate", "code")
        .agg(pl.col("votes").sum())
    )
    keep = []
    for (day, chamber, district, special), lines in frame.group_by("day", "chamber", "district", "special"):
        contest = f"{day}:WA:" + ("senate" if chamber == "senate" else f"house:{district}") + (":special" if special else "")
        sums = sorted(total for total in lines.group_by("candidate").agg(pl.col("votes").sum())["votes"].to_list() if total > 0)
        filed = sorted(total for total in known.get(contest, []) if total > 0)
        if sums == filed:
            keep.append(lines)
        else:
            print(f"{contest}: county sums {sums} against the FEC's {filed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(keep).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        state=pl.lit("WA"),
        measure=pl.lit(1, dtype=pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        kind=pl.lit("county"),
        source=pl.lit("WA"),
    ).write_parquet(PARSED / "counties.parquet")
    pl.DataFrame(turnout, schema=["day", "code", "registered", "ballots"], orient="row").write_parquet(
        PARSED / "turnout.parquet"
    )
    print(f"{sum(len(lines['candidate'].unique()) for lines in keep)} candidate lines kept in {len(keep)} contests")


def emit() -> Path:
    voters = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2008, 2010, and 2012 U.S. House and Senate primaries by county, with turnout",
            polity="US-WA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.vote.wa.gov/results/",
            license="Public record",
        )
        for day in DAYS:
            for url in [*urls(day), BASE.format(day=day) + "Turnout.html"]:
                cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WA": source}, strict=True)
        units = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.county' and code like '53%%'").fetchall()
        )
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for day in DAYS:
            ((election,),) = {
                (election_id,)
                for (election_id,) in conn.execute(
                    """select c.election_id from external_id x join contest c on c.id = x.entity_id
                       where x.scheme = 'us.contest' and x.value like %s""",
                    [f"{day}:WA:%"],
                )
            }
            result_set = cs.key(f"{SOURCE}.turnout", str(day))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election not in held,
                completeness=1,
            )
            for code, registered, ballots in voters.filter(pl.col("day") == day).select("code", "registered", "ballots").iter_rows():
                for measure, value in ((2, registered), (3, ballots)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=UUID(str(units[code])),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
