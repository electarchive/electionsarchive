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
YEARS = {"NH": range(2000, 2023, 2), "VT": range(1990, 2023, 2)}
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
WINNERS = (
    "Winners are those declared in Klarner's state legislative election returns for this race. The state's election "
    "archive marks only who led in each town, not who won the seat."
)
DOUBLED = (
    "Klarner's state legislative election returns give every candidate in this race exactly twice the votes in the "
    "town and ward results here."
)


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
        for year in YEARS[state]:
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


JOURNAL = "https://legislature.vermont.gov/Documents/2018/Docs/JOURNAL/hj170104.pdf"
RECOUNTS = {
    "2016-11-08:VT:house:windsororange-1:g": (
        {"page": 10},
        (
            "Town canvass totals show Sarah Buxton leading David Ainsworth 1,003 to 1,000. Following a recount requested by "
            "Ainsworth, Superior Court Judge Robert Gerety ruled in December 2016 that Ainsworth received 1,005 votes and "
            "Buxton received 1,003. The House seated Ainsworth for Windsor-Orange-1 on January 4, 2017. Because the "
            "court's recount order is not published online, these totals are not stored as a separate result set."
        ),
    ),
    "2016-11-08:NH:house:hillsborough-12:g": (
        None,
        (
            "Town canvass totals show Lisa M. Freeman leading Andrew Gregoire for the second seat by 6 votes, 1,128 to "
            "1,122. The winners shown are those declared in Klarner's state legislative election returns, which list "
            "Gregoire as elected. No recount record for this race could be found online."
        ),
    ),
    "2016-11-08:NH:house:hillsborough-28:g": (
        None,
        (
            "Town canvass totals show Elizabeth Ferreira leading Sylvia E. Gale for the third seat by 6 votes, 2,420 to "
            "2,414. The winners shown are those declared in Klarner's state legislative election returns, which list "
            "Gale as elected. No recount record for this race could be found online."
        ),
    ),
}


def fetch() -> None:
    raw.fetch(SOURCE, JOURNAL, suffix=".pdf")
    for state in GRAPHQL:
        for year in YEARS[state]:
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
    towns[("VT", elstats.plain("Essex Junction"))] = "50007"
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
            "1990–2022, from the states' ElectionStats archives",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^(199[0-9]|20([01][0-9]|2[0-2]))-1[01]-\\d\\d:(NH|VT):'
                     and rs.changeset_id not in (select id from changeset where pipeline = %s)
                     and rs.status = 'active' and rs.completeness >= 0.98
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
        published = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"])
        }
        noted = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join external_id x on x.entity_id = n.entity_id
                   where x.scheme = %s and n.body_md = %s""",
                [f"{SOURCE}.result_set", WINNERS],
            ).fetchall()
        )
        flagged = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join external_id x on x.entity_id = n.entity_id
                   where x.scheme = %s and n.kind = 'caveat' and n.body_md like 'Klarner''s state legislative election returns give every candidate%%'""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        )
        journal = cs.source("Vermont House of Representatives", "Journal of the House, January 4, 2017")
        if journal in cs.minted:
            cs.add(
                "source",
                id=journal,
                kind="official_body",
                publisher="Vermont House of Representatives",
                title="Journal of the House, January 4, 2017",
                url=JOURNAL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, JOURNAL), journal)
        cited = {
            (str(entity), str(source))
            for entity, source in conn.execute("select entity_id, source_id from citation").fetchall()
        }
        caveats = {
            (str(entity), body)
            for entity, body in conn.execute("select entity_id, body_md from note where kind = 'caveat'").fetchall()
        }
        memberships: set[tuple[str, str, date]] = set()
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
            contest, election, district, default, first, official = conn.execute(
                """select c.id, c.election_id, c.district_area_id, rs.id, min(v.count_round),
                          sum(v.value) filter (where v.measure = 1)::bigint
                   from external_id x join contest c on c.id = x.entity_id
                   join lateral (select r.id, r.k from result_set r
                                 where r.contest_id = c.id and r.status = 'active' and r.kind = 'counted'
                                   and not exists (select from changeset z where z.id = r.changeset_id
                                                   and z.pipeline = %s)
                                 order by r.is_default desc, r.k limit 1) rs on true
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s group by 1, 2, 3, 4""",
                [SOURCE, key],
            ).fetchone() or (None, None, None, None, None, None)
            if contest is None or not official or first not in (0, 1):
                skipped.setdefault("no contest on file", []).append(key)
                continue
            options = conn.execute(
                """select o.id, o.ballot_label,
                          coalesce((select sum(v.value) from result_value v
                                    where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o join result_set rs on rs.id = %s
                   where o.contest_id = %s and o.parent_option_id is null""",
                [default, contest],
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
                    hits = [name for name, total in sums.items() if votes in (total, 2 * total) and name not in matched]
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
                doubled = bool(off) and all(votes == 2 * sums[name] for name, (_, votes) in matched.items())
                if stray or (off and not doubled):
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
                    completeness=round(sum(sums[name] for name in matched) / official * (2 if doubled else 1), 4),
                )
                if doubled:
                    cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
                    if (str(result_set), DOUBLED) not in caveats:
                        cs.add(
                            "note",
                            **({"id": flagged[result_set]} if result_set in flagged else {}),
                            entity_id=result_set,
                            kind="caveat",
                            body_md=DOUBLED,
                        )
                picked = rows.filter(pl.col("name").is_in(list(matched)), pl.col("votes") > 0).with_columns(
                    code=pl.concat_str(
                        pl.lit(str(day.year)), pl.lit(state), "fips", pl.lit(""), "precinct", separator=":"
                    )
                )
                for unit, fips in picked.select("code", "fips").unique().iter_rows():
                    minted.setdefault(unit, (state, fips, day))
                    if district is not None:
                        memberships.add((unit, str(district), day))
                declared = conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [default]
                ).fetchall()
                for option, kind, seats in declared:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
                if declared:
                    cs.add(
                        "note",
                        **({"id": noted[result_set]} if result_set in noted else {}),
                        entity_id=result_set,
                        kind="methodology",
                        body_md=WINNERS,
                    )
                if key in RECOUNTS:
                    locator, caveat = RECOUNTS[key]
                    if (str(result_set), caveat) not in caveats:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
                    for entity in (result_set, default):
                        if locator is not None and (str(entity), str(journal)) not in cited:
                            cs.add("citation", entity_id=entity, source_id=journal, locator=locator)
                if f"{key}:precinct" in published:
                    continue
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
        if memberships:
            cs.frame(
                "reporting_unit_area",
                pl.DataFrame(
                    [
                        (str(cs.unit("us.precinct", unit)), area, day, day + timedelta(days=1))
                        for unit, area, day in sorted(memberships)
                    ],
                    schema={"unit_id": pl.String, "area_id": pl.String, "valid_from": pl.Date, "valid_to": pl.Date},
                    orient="row",
                ),
            )
        for state in GRAPHQL:
            for year in YEARS[state]:
                cs.artifact(raw.cached(SOURCE, search(state, year)), sources[state])
        print(f"{len(values)} precinct sets, {len(minted) - len(existing)} new precincts, {len(existing)} reused")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
