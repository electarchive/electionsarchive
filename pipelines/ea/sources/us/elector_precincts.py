from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.elector_precincts"
VERSION = "1"
CONTESTS = {
    ("2016-11-08", "ME"): 2,
    ("2020-11-03", "ME"): 2,
    ("2016-11-08", "NE"): 3,
    ("2020-11-03", "NE"): 3,
}
METHOD = (
    "Precinct returns from the Voting and Election Science Team's statewide presidential results, each precinct "
    "placed in the congressional district whose shape for that election holds the precinct's interior point (or, "
    "for a coastal precinct whose point falls outside every district shape, the nearest district). Maine keeps its "
    "towns whole and Nebraska its precincts whole within a district, so no precinct is divided."
)


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by precinct for Maine's and Nebraska's congressional district electors, 2016 and 2020, from the Voting and Election Science Team's precinct returns",
            polity="US",
        )
        notes = dict(
            conn.execute(
                """select n.entity_id, n.id from note n join changeset c on c.id = n.changeset_id
                   where c.pipeline = %s and n.kind = 'methodology'""",
                [SOURCE],
            ).fetchall()
        )
        for (day, state), count in CONTESTS.items():
            statewide = f"{day}:{state}:president"
            ((source, election),) = conn.execute(
                """select r.source_id, r.election_id from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                   join changeset c on c.id = r.changeset_id and c.pipeline = 'us.vest'
                   where x.scheme = 'us.contest' and x.value = %s""",
                [statewide],
            ).fetchall()
            values = pl.DataFrame(
                conn.execute(
                    """with p as (
                         select u.id as unit, u.k, coalesce(g.label_point, st_pointonsurface(g.geom)) as pt
                         from reporting_unit u join area_geometry g on g.area_id = u.area_id
                         where u.k in (select v.unit_k from result_value v
                                       join result_set r on r.k = v.result_set_k and r.status = 'active'
                                       join changeset c on c.id = r.changeset_id and c.pipeline = 'us.vest'
                                       join external_id x on x.entity_id = r.contest_id
                                       where x.scheme = 'us.contest' and x.value = %(statewide)s)
                       ), d as (
                         select c.code, g.geom from area a join area_code c on c.area_id = a.id
                         join area_geometry g on g.area_id = a.id
                         where a.scheme_code = 'us.cd' and c.code ~ %(codes)s and g.valid @> %(day)s::date
                       )
                       select p.unit::text,
                           (select d.code from d order by not st_contains(d.geom, p.pt), st_distance(d.geom, p.pt) limit 1)
                               as district,
                           o.kind, cy.person_id::text as person, v.value::bigint as votes
                       from p join result_value v on v.unit_k = p.k and v.measure = 1
                       join result_set r on r.k = v.result_set_k and r.status = 'active'
                       join changeset c on c.id = r.changeset_id and c.pipeline = 'us.vest'
                       join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest' and x.value = %(statewide)s
                       join contest_option o on o.k = v.option_k
                       left join candidacy cy on cy.option_id = o.id and cy.role = 'head'""",
                    {"statewide": statewide, "codes": f"^{ {'ME': '23', 'NE': '31'}[state] }0[1-9]$", "day": day},
                ).fetchall(),
                schema=["unit", "district", "kind", "person", "votes"],
                orient="row",
            )
            for number in range(1, count + 1):
                key = f"{day}:{state}-{number:02d}:president"
                district = f"{ {'ME': '23', 'NE': '31'}[state] }{number:02d}"
                ((contest,),) = conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value = %s", [key]
                ).fetchall()
                options = {
                    person: (str(option), official)
                    for option, person, official in conn.execute(
                        """select o.id, cy.person_id::text, (select sum(v.value) from result_value v
                               join result_set r on r.k = v.result_set_k and r.is_default and r.contest_id = o.contest_id
                               where v.option_k = o.k and v.measure = 1)::bigint
                           from contest_option o join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                           where o.contest_id = %s and o.kind <> 'write_in'""",
                        [contest],
                    ).fetchall()
                }
                part = values.filter(pl.col("district") == district)
                unmatched = part.filter(pl.col("person").is_not_null() & ~pl.col("person").is_in(list(options)))
                if unmatched.height:
                    raise ValueError(f"{key}: {unmatched['person'].unique().to_list()} match no district option")
                part = part.with_columns(
                    option=pl.col("person").replace_strict(
                        {person: option for person, (option, _) in options.items()}, default=None
                    )
                )
                if part["option"].is_null().any():
                    pool = cs.key("us.option", f"{key}:write_in:{SOURCE.removeprefix('us.')}")
                    if pool in cs.minted:
                        cs.add("contest_option", id=pool, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    part = part.with_columns(pl.col("option").fill_null(str(pool)))
                total = 0
                for person, (option, official) in options.items():
                    counted = int(part.filter(pl.col("option") == option)["votes"].sum())
                    total += official or 0
                    if official and abs(counted - official) > max(200, official * 0.005):
                        raise ValueError(f"{key}: {person} has {counted} by precinct against {official}")
                    print(f"{key}: {counted} by precinct, {official} declared")
                result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=False,
                    completeness=round(int(part["votes"].sum()) / total, 4),
                )
                cs.add("note", id=notes.get(result_set), entity_id=result_set, kind="methodology", body_md=METHOD)
                cs.frame(
                    "result_value",
                    part.group_by("unit", "option")
                    .agg(pl.col("votes").sum())
                    .select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit",
                        option_id="option",
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.lit(1, pl.Int16),
                        count_round=pl.lit(0, pl.Int16),
                        flags=pl.lit(0, pl.Int16),
                        value="votes",
                    ),
                )
        return cs.write()
