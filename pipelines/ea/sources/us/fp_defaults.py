from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.fp_defaults"
VERSION = "1"
PIPELINES = (
    "us.oe_federal",
    "us.heda",
    "us.swdb_sov",
    "us.elstats_precincts",
    "us.elector_precincts",
    "us.al_precincts",
    "us.la_precincts",
    "us.il_cook_dupage_2022",
)
COLUMNS = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Precinct result sets that cover at least 98 percent of the official total become the default, and the earlier default stays active and non-default",
            polity="US",
        )
        targets = conn.execute(
            f"""select {", ".join(f"rs.{column}" for column in COLUMNS)} from result_set rs
                join changeset c on c.id = rs.changeset_id
                join external_id x on x.entity_id = rs.contest_id and x.scheme = 'us.contest'
                where rs.status = 'active' and not rs.is_default and rs.kind = 'counted'
                  and rs.completeness between 0.98 and 1.05
                  and x.value ~ ':(president|governor|senate|house:[0-9]+)(:special)?$'
                  and c.pipeline = any(%s)
                  and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                              where v.result_set_k = rs.k and u.scheme_code in ('us.precinct', 'us.nyc_ed'))
                  and not exists (select from result_set d join result_value v on v.result_set_k = d.k
                                  join reporting_unit u on u.k = v.unit_k
                                  where d.contest_id = rs.contest_id and d.stage = rs.stage and d.is_default and d.status = 'active'
                                    and u.scheme_code in ('us.precinct', 'us.nyc_ed', 'us.county_part'))
                order by rs.completeness desc""",
            [list(PIPELINES)],
        ).fetchall()
        chosen: set[tuple[object, object]] = set()
        for row in targets:
            target = dict(zip(COLUMNS, row, strict=True))
            if (target["contest_id"], target["stage"]) in chosen:
                continue
            chosen.add((target["contest_id"], target["stage"]))
            for prior in conn.execute(
                f"select {', '.join(COLUMNS)} from result_set where contest_id = %s and stage = %s and is_default and id <> %s",
                [target["contest_id"], target["stage"], target["id"]],
            ).fetchall():
                cs.add("result_set", **dict(zip(COLUMNS, prior, strict=True)), is_default=False)
            cs.add("result_set", **target, is_default=True)
            for option, kind, seats in conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default
                   where rs.contest_id = %s and rs.stage = %s and rs.id <> %s""",
                [target["contest_id"], target["stage"], target["id"]],
            ).fetchall():
                cs.add("outcome", result_set_id=target["id"], option_id=option, kind=kind, seats=seats)
        print(len(chosen), "result sets promoted")
        return cs.write()
