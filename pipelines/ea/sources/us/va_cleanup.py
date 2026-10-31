from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.va_cleanup"
VERSION = "2"
PHANTOMS = ("Joe Turnermay", "Connie Printzhutchinson", "Gregg Garrett Nelson")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline="us.klarner",
            version=VERSION,
            summary="Withdraw Klarner's Virginia 2019 SD33 and HD86 sets and retire the candidates that exist only there",
            polity="US-VA",
            kind="correction",
        )
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "source_id",
            "as_of",
            "is_default",
            "completeness",
            "supersedes_id",
        )
        sets = conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                join changeset c on c.id = r.changeset_id
                where x.value in ('2019-11-05:VA:senate:33:g', '2019-11-05:VA:house-of-delegates:86:g')
                  and r.status = 'active' and not r.is_default and c.pipeline = 'us.klarner'"""
        ).fetchall()
        for row in sets:
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="withdrawn")
        values = pl.DataFrame(
            conn.execute(
                """select r.id::text as result_set_id, u.id::text as unit_id, o.id::text as option_id, v.channel, v.measure,
                          v.count_round, v.flags, v.value
                   from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k join contest_option o on o.k = v.option_k
                   where r.id = any(%s) and o.ballot_label <> all(%s)""",
                [[row[0] for row in sets], list(PHANTOMS)],
            ).fetchall(),
            schema=["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags", "value"],
            orient="row",
        )
        cs.frame("result_value", values)
        for candidacy, option, person, label in conn.execute(
            """select c.id, o.id, c.person_id, o.ballot_label from contest_option o
               join candidacy c on c.option_id = o.id where o.ballot_label = any(%s)""",
            [list(PHANTOMS)],
        ).fetchall():
            for entity in (candidacy, option, person):
                cs.add(
                    "entity_retirement",
                    entity_id=entity,
                    reason=f"{label} is not on the certified ballot; a Klarner parse artifact",
                )
        return cs.write()
