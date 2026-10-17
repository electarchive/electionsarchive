import io
import json
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats
from ea.store import DATA, connect

SOURCE = "us.elstats_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = range(2000, 2017, 2)
OFFICES = {8: "house", 9: "senate"}
GRAPHQL = {
    "NH": "https://nh2.elstats3.civera.com/api/graphql_pr",
    "VT": "https://electionarchive.vermont.gov/api/graphql_pr",
}
QUERY = """query Contest($contestId: Int!) {
  contestGranularData(contestId: $contestId, voteChannels: false, splitParty: false) {
    candidates { candidateId candidate { pseudocandidate displayName } }
    divisions {
      division { id name divisionTypeName }
      children {
        division { id name divisionTypeName }
        granularRow { candidateId votes }
        children { division { id name divisionTypeName } granularRow { candidateId votes } }
      }
    }
  }
}"""
TOLERANCE = 0.005


def search(state: str, year: int) -> str:
    body = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"id": office} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {"search": json.dumps(body, separators=(",", ":"))}
    )


def contests() -> pl.DataFrame:
    frames = []
    for state in GRAPHQL:
        for year in YEARS:
            frames.append(
                pl.read_csv(raw.cached(SOURCE, search(state, year)).path, infer_schema=False)
                .filter(
                    pl.col("election_type") == "General Election",
                    pl.col("office_id").is_in([str(o) for o in OFFICES]),
                )
                .select(
                    pl.lit(state).alias("state"),
                    pl.col("contest_id").cast(pl.Int64).alias("contest"),
                    pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
                    pl.col("office_id").cast(pl.Int64).replace_strict(OFFICES, return_dtype=pl.String).alias("chamber"),
                    pl.col("district_name").alias("district"),
                )
                .unique()
            )
    return pl.concat(frames).sort("state", "contest")


def fetch() -> None:
    for state in GRAPHQL:
        for year in YEARS:
            raw.fetch(SOURCE, search(state, year), suffix=".csv")
    for state, contest in contests().select("state", "contest").iter_rows():
        body = {"query": QUERY, "variables": {"contestId": contest}}
        try:
            raw.cached(SOURCE, GRAPHQL[state], body)
        except LookupError:
            raw.fetch(SOURCE, GRAPHQL[state], suffix=".json", body=body)
            time.sleep(1)


def parse() -> None:
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False)
    towns: dict[tuple[str, str], str] = {}
    for state, geoid, name in (
        gazetteer.filter(pl.col("USPS").is_in(list(GRAPHQL))).select("USPS", "GEOID", "NAME").iter_rows()
    ):
        towns.setdefault((state, elstats.plain(name)), geoid[:5])
        towns.setdefault((state, elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower()))), geoid[:5])
    towns.update(elstats.FORMER)
    rows = []
    unknown: set[tuple[str, str]] = set()
    for state, contest, day, chamber, district in contests().iter_rows():
        body = {"query": QUERY, "variables": {"contestId": contest}}
        data = json.loads(raw.cached(SOURCE, GRAPHQL[state], body).path.read_text())
        data = data.get("data", data)["contestGranularData"]
        names = {
            entry["candidateId"]: entry["candidate"]["displayName"]
            for entry in data["candidates"]
            if entry["candidate"]["pseudocandidate"] in (None, "ALL_OTHER_CANDIDATES")
        }
        for town in data["divisions"]["children"]:
            name = town["division"]["name"]
            fips = towns.get((state, elstats.plain(name))) or towns.get(
                (state, elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())))
            )
            if fips is None:
                unknown.add((state, name))
                continue
            for leaf, label in (
                [(town, name)]
                if not town["children"]
                else [
                    (
                        part,
                        f"{name} - {part['division']['divisionTypeName']} {part['division']['name']}"
                        if part["division"]["name"].isdigit()
                        else f"{name} - {part['division']['name']}",
                    )
                    for part in town["children"]
                ]
            ):
                for row in leaf["granularRow"]:
                    if row["candidateId"] in names:
                        rows.append(
                            (
                                state,
                                contest,
                                day,
                                chamber,
                                district,
                                fips,
                                label,
                                names[row["candidateId"]],
                                row["votes"],
                            )
                        )
    if unknown:
        print(f"unknown towns: {sorted(unknown)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema=["state", "contest", "day", "chamber", "district", "fips", "precinct", "name", "votes"],
        orient="row",
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire and Vermont House and Senate general elections by town, ward, and polling place "
            "2000–2016, from the states' ElectionStats archives",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^20(0[0-9]|1[0-6])-1[01]-\\d\\d:(NH|VT):'
                     and rs.changeset_id not in (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        sources = {state: cs.source(elstats.TENANTS[state][1], elstats.TITLE) for state in GRAPHQL}
        if cs.minted & set(sources.values()):
            raise ValueError(f"the {elstats.TITLE} sources must already exist")
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' "
                "and (code like '33%%' or code like '50%%')"
            ).fetchall()
        }
        minted: dict[str, tuple[str, str, date]] = {}
        skipped: dict[str, list[str]] = {}
        values = []
        for (state, _, day, chamber, district), rows in lines.group_by(
            "state", "contest", "day", "chamber", "district", maintain_order=True
        ):
            found = re.fullmatch(r"(\D*?) ?(?:(\d+)(?:-(\d+))?)?", district.strip())
            assert found is not None
            code = re.sub(r"[^a-z]", "", found[1].lower())
            if found[2]:
                code += ("-" if code else "") + str(int(found[2])) + (f"-{chr(96 + int(found[3]))}" if found[3] else "")
            key = f"{day.isoformat()}:{state}:{chamber}:{code}:g"
            if key in filled:
                skipped.setdefault("already has a precinct set", []).append(key)
                continue
            contest, election, first, official = conn.execute(
                """select c.id, c.election_id, min(v.count_round), sum(v.value) filter (where v.measure = 1)::bigint
                   from external_id x join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s group by 1, 2""",
                [key],
            ).fetchone() or (None, None, None, None)
            if contest is None or not official or first not in (0, 1):
                skipped.setdefault("no contest on file", []).append(key)
                continue
            options = conn.execute(
                """select o.id, o.ballot_label,
                          coalesce((select sum(v.value) from result_value v
                                    where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            sums = dict(rows.group_by("name").agg(pl.col("votes").sum()).iter_rows())
            matched: dict[str, tuple[str, int]] = {}
            for option, label, votes in options:
                if not votes:
                    continue
                words = [
                    w
                    for w in re.sub(r"[^a-z ]", "", re.sub(r"\bst\.? (?=[a-z])", "saint", label.lower())).split()
                    if w not in {"jr", "sr", "ii", "iii"}
                ]
                pool = re.search(r"write|scatter|other", label.lower()) is not None
                hits = [
                    name
                    for name in sums
                    if (pool and name in ("Write-Ins", "All Other Votes"))
                    or (
                        not pool
                        and words
                        and (
                            parts := [
                                w
                                for w in re.sub(
                                    r"[^a-z ]", "", re.sub(r"\bst\.? (?=[a-z])", "saint", name.lower())
                                ).split()
                                if w not in {"jr", "sr", "ii", "iii"}
                            ]
                        )
                        and parts[-1] == words[-1]
                        and (len(words) == 1 or parts[0][0] == words[0][0])
                    )
                ]
                if not hits and not pool:
                    hits = [name for name, total in sums.items() if total == votes and name not in matched]
                if len(hits) != 1:
                    break
                matched[hits[0]] = (str(option), votes)
            else:
                stray = sum(
                    votes
                    for name, votes in sums.items()
                    if name not in matched and name not in ("Write-Ins", "All Other Votes")
                )
                off = [
                    name for name, (_, votes) in matched.items() if abs(sums[name] - votes) > max(10, votes * TOLERANCE)
                ]
                if stray or off:
                    skipped.setdefault("a precinct sum misses the official total", []).append(key)
                    continue
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[state],
                    is_default=False,
                    completeness=round(sum(sums[name] for name in matched) / official, 4),
                )
                picked = rows.filter(pl.col("name").is_in(list(matched)), pl.col("votes") > 0).with_columns(
                    code=pl.concat_str(
                        pl.lit(str(day.year)), pl.lit(state), "fips", pl.lit(""), "precinct", separator=":"
                    )
                )
                for unit, fips in picked.select("code", "fips").unique().iter_rows():
                    minted.setdefault(unit, (state, fips, day))
                values.append(
                    picked.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("code").map_elements(
                            lambda unit: str(cs.unit("us.precinct", unit)), return_dtype=pl.String
                        ),
                        option_id=pl.col("name").replace_strict({name: pair[0] for name, pair in matched.items()}),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.lit(1, pl.Int16),
                        count_round=pl.lit(first, pl.Int16),
                        flags=pl.lit(0, pl.Int16),
                        value="votes",
                    )
                )
                continue
            skipped.setdefault("a candidate does not match", []).append(key)
        for reason, keys in skipped.items():
            print(f"{len(keys)} contests skipped: {reason}", keys[:8])
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code = any(%s)", [list(minted)]
            ).fetchall()
        }
        for code, (state, fips, day) in minted.items():
            if code in existing:
                continue
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", code),
                polity_id=cs.polity(f"US-{state}"),
                scheme_code="us.precinct",
                code=code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for state in GRAPHQL:
            for year in YEARS:
                cs.artifact(raw.cached(SOURCE, search(state, year)), sources[state])
        print(f"{len(values)} precinct sets, {len(minted) - len(existing)} new precincts, {len(existing)} reused")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
