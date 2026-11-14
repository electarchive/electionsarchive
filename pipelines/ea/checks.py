import json
import sys
import time
from dataclasses import dataclass, field
from typing import LiteralString
from uuid import UUID

from jsonschema import Draft7Validator
from jsonschema.exceptions import best_match
from psycopg import errors, sql

from ea.names import ACRONYMS, ROMAN
from ea.store import LAUNCHED_AT, ROOT, Connection

FACTS = ("result_value", "result_flow", "value_locator")
KEYS = {
    "result_set_id": "result_set",
    "unit_id": "reporting_unit",
    "option_id": "contest_option",
    "from_option_id": "contest_option",
}
REPLACEABLE = ("area_geometry", "area_parent", "tenure_affiliation")
DROPPABLE = ("area_code", "area_parent", "area_geometry", "area_kind", "area_membership")
SWEEPABLE = {
    "party_image": "id",
    "party_founding": "party_id",
    "party_affiliation": "party_id",
    "election_cadence": "id",
    "results_access": "id",
    "census_tier": "id",
    "census_tier_count": "id",
    "census_class": "id",
    "census_match": "id",
    "census_date": "id",
    "body_composition": "id",
    "body_composition_party": "id",
    "note": "entity_id",
}
TIMEOUT = "20min"
RULES = Draft7Validator(json.loads((ROOT / "packages" / "engine" / "rules.schema.json").read_text()))


def references(conn: Connection) -> list[tuple[str, str, str]]:
    return conn.execute(
        """select c.relname, a.attname, substring(d.description from '^references (\\w+) \\(k\\)$')
           from pg_description d
           join pg_class c on c.oid = d.objoid and d.classoid = 'pg_class'::regclass
           join pg_attribute a on a.attrelid = c.oid and a.attnum = d.objsubid
           where c.relnamespace = 'public'::regnamespace and not c.relispartition
             and d.description ~ '^references \\w+ \\(k\\)$'
           order by 1, 2"""
    ).fetchall()


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    entity_id: UUID | None
    result_set_id: UUID | None
    details: dict[str, object]


@dataclass(frozen=True)
class Stage:
    schema: str
    tables: dict[str, list[str]]
    entities: set[str]
    pipeline: str
    polity: str
    temporal: dict[str, list[str]]
    parents: dict[str, str]
    owners: dict[str, str]
    replaces: dict[str, list[str]]
    nullable: dict[str, set[str]] = field(default_factory=dict)
    clears: list[str] = field(default_factory=list)
    drops: dict[str, dict[str, object]] = field(default_factory=dict)
    sweeps: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if unknown := sorted(set(self.drops) - set(DROPPABLE)):
            raise ValueError(f"scope.drops may name only {', '.join(DROPPABLE)}, not {', '.join(unknown)}")
        if unstaged := sorted(set(self.drops) - set(self.tables)):
            raise ValueError(f"scope.drops names {', '.join(unstaged)}, which the changeset does not stage")
        if unknown := sorted(set(self.sweeps) - set(SWEEPABLE)):
            raise ValueError(f"scope.sweeps may name only {', '.join(SWEEPABLE)}, not {', '.join(unknown)}")
        if unstaged := sorted(set(self.sweeps) - set(self.tables)):
            raise ValueError(f"scope.sweeps names {', '.join(unstaged)}, which the changeset does not stage")
        if unknown := sorted(set(self.replaces) - set(REPLACEABLE)):
            raise ValueError(f"scope.replaces may name only {', '.join(REPLACEABLE)}, not {', '.join(unknown)}")

    def writers(self, table: str) -> sql.Composable:
        return sql.Literal([self.pipeline, *self.replaces.get(table, [])])

    def dropped(self, table: str) -> sql.Composable:
        drop = self.drops[table]
        return sql.SQL(
            """p.changeset_id = c.id and c.pipeline = {pipeline} and p.area_id = any({areas}::uuid[])
               and p.valid_from >= {start}::date"""
        ).format(pipeline=sql.Literal(self.pipeline), areas=sql.Literal(drop["areas"]), start=sql.Literal(drop["from"]))

    def unstaged(self, table: str) -> sql.Composable:
        return sql.SQL(
            """c.id = p.changeset_id and c.pipeline = any({writers})
               and p.tenure_id in (select tenure_id from {staged}{kept})
               and not exists (select 1 from {staged} x where {same})"""
        ).format(
            writers=self.writers(table),
            staged=self.staged(table),
            kept=sql.SQL(" union select id from {}").format(self.staged("tenure"))
            if "tenure" in self.tables
            else sql.SQL(""),
            same=sql.SQL(" and ").join(
                sql.SQL("x.{0} is not distinct from p.{0}").format(sql.Identifier(column))
                for column in self.tables[table]
            ),
        )

    def swept(self, table: str) -> sql.Composable:
        if table == "note":
            return sql.SQL(
                """p.changeset_id = c.id and c.pipeline = {pipeline}
                   and exists (select from {staged} s where s.entity_id = p.entity_id and s.kind = p.kind)
                   and not exists (select from {staged} s where {same})"""
            ).format(
                pipeline=sql.Literal(self.pipeline),
                staged=self.staged(table),
                same=sql.SQL(" and ").join(
                    sql.SQL("s.{0} is not distinct from p.{0}").format(sql.Identifier(column))
                    for column in self.tables[table]
                    if column != "id"
                ),
            )
        return sql.SQL(
            "p.changeset_id = c.id and c.pipeline = {} and not exists (select from {} s where s.{key} = p.{key})"
        ).format(sql.Literal(self.pipeline), self.staged(table), key=sql.Identifier(SWEEPABLE[table]))

    def staged(self, table: str) -> sql.Composable:
        return sql.SQL("{}.{}").format(sql.Identifier(self.schema), sql.Identifier(table))

    def overlaps(self, table: str) -> sql.Composable:
        columns = self.tables[table]
        return sql.SQL(
            """{keys} and p.valid && daterange({start}, {to}, '[)')
               and not exists (select 1 from {staged} x where {same})"""
        ).format(
            keys=sql.SQL(" and ").join(
                sql.SQL(
                    "p.{0} is not distinct from s.{0}" if key in self.nullable.get(table, set()) else "p.{0} = s.{0}"
                ).format(sql.Identifier(key))
                for key in self.temporal[table]
            ),
            start=sql.SQL("s.valid_from" if "valid_from" in columns else "null::date"),
            to=sql.SQL("s.valid_to" if "valid_to" in columns else "null::date"),
            staged=self.staged(table),
            same=sql.SQL(" and ").join(
                sql.SQL(
                    "x.{0} = p.{0}"
                    if column in self.temporal[table] and column not in self.nullable.get(table, set())
                    else "x.{0} is not distinct from p.{0}"
                ).format(sql.Identifier(column))
                for column in columns
            ),
        )

    def replaced(self, table: str) -> sql.Composed:
        return sql.SQL(
            """select r.k from public.result_set r join public.changeset c on c.id = r.changeset_id
               where c.pipeline = {} and r.id in (select result_set_id from {} union all select unnest({}::uuid[]))"""
        ).format(sql.Literal(self.pipeline), self.staged(table), sql.Literal(self.clears))

    def stale(self, table: str) -> sql.Composable:
        parent = sql.Identifier(self.parents[table])
        return sql.SQL(
            """c.id = {owner} and c.pipeline = {pipeline}
               and p.{parent} in (select {parent} from {staged}{cleared})
               and not exists (select from {staged} x where x.{parent} = p.{parent} and {same})"""
        ).format(
            cleared=sql.SQL(
                """ union all select r.id from public.result_set r join public.changeset w on w.id = r.changeset_id
                               where w.pipeline = {pipeline} and r.id = any({clears}::uuid[])"""
            ).format(parent=parent, pipeline=sql.Literal(self.pipeline), clears=sql.Literal(self.clears))
            if self.parents[table] == "result_set_id" and self.clears
            else sql.SQL(""),
            owner=sql.SQL("(select r.changeset_id from public.{} r where r.id = p.{})").format(
                sql.Identifier(self.owners[table]), parent
            )
            if table in self.owners
            else sql.SQL("p.changeset_id"),
            pipeline=sql.Literal(self.pipeline),
            parent=parent,
            staged=self.staged(table),
            same=sql.SQL(" and ").join(
                sql.SQL("x.{0} is not distinct from p.{0}").format(sql.Identifier(column))
                for column in self.tables[table]
            ),
        )

    def either(
        self, table: str, *columns: LiteralString, among: tuple[LiteralString, sql.Composable] | None = None
    ) -> sql.Composable:
        listed = sql.SQL(", ").join(map(sql.Identifier, columns))
        public = sql.SQL("select {} from public.{} p where true").format(listed, sql.Identifier(table))
        if among is not None:
            public = sql.SQL("{} and p.{} in ({})").format(public, sql.Identifier(among[0]), among[1])
        if table not in self.tables:
            return sql.SQL("({})").format(public)
        if columns[0] == "id":
            public = sql.SQL("{} and not exists (select from {} x where x.id = p.id)").format(
                public, self.staged(table)
            )
        staged = sql.SQL(", ").join(
            sql.Identifier(column)
            if column in self.tables[table]
            else sql.SQL("null as {}").format(sql.Identifier(column))
            for column in columns
        )
        return sql.SQL("(select {} from {} union all {})").format(staged, self.staged(table), public)


def _accounting(stage: Stage) -> list[tuple[str, sql.Composed]]:
    staged = stage.tables.get("result_set", [])
    empty = (
        [
            (
                "accounting.empty",
                sql.SQL(
                    """select null::uuid, s.id, jsonb_build_object('completeness', s.completeness)
                       from {result_sets} s left join public.result_set p on p.id = s.id
                       where {kind} = 'counted' and {status} = 'active' and s.completeness > 0{unstaged}
                         and (s.id = any({clears}::uuid[]) or p.k is null
                              or not exists (select from public.result_value v where v.result_set_k = p.k))"""
                ).format(
                    result_sets=stage.staged("result_set"),
                    kind=sql.SQL("s.kind" if "kind" in staged else "coalesce(p.kind, 'counted')"),
                    status=sql.SQL("s.status" if "status" in staged else "coalesce(p.status, 'active')"),
                    unstaged=sql.SQL(" and not exists (select from {} v where v.result_set_id = s.id)").format(
                        stage.staged("result_value")
                    )
                    if "result_value" in stage.tables
                    else sql.SQL(""),
                    clears=sql.Literal(stage.clears),
                ),
            )
        ]
        if "completeness" in staged
        else []
    )
    if "result_value" not in stage.tables:
        return empty
    facts = stage.staged("result_value")
    result_sets = stage.either(
        "result_set", "id", "contest_id", among=("id", sql.SQL("select distinct result_set_id from {}").format(facts))
    )
    contests = stage.either(
        "contest", "id", "magnitude", among=("id", sql.SQL("select contest_id from {}").format(result_sets))
    )
    return [
        *empty,
        (
            "accounting.votes_valid",
            sql.SQL(
                """with v as (
                     select result_set_id, unit_id,
                            sum(value) filter (where measure = 1 and option_id is not null) as votes,
                            sum(value) filter (where measure = 4 and option_id is null) as valid
                     from {facts} where count_round = 0 group by 1, 2
                   )
                   select null::uuid, v.result_set_id,
                          jsonb_build_object('unit_id', v.unit_id, 'votes', v.votes, 'valid', v.valid)
                   from v join {result_sets} r on r.id = v.result_set_id join {contests} c on c.id = r.contest_id
                   where c.magnitude = 1 and v.valid is not null and coalesce(v.votes, 0) <> v.valid"""
            ).format(facts=facts, result_sets=result_sets, contests=contests),
        ),
        (
            "accounting.ballots_sum",
            sql.SQL(
                """with v as (
                     select result_set_id, unit_id,
                            sum(value) filter (where measure = 3) as ballots_cast,
                            sum(value) filter (where measure = 4) as valid,
                            sum(value) filter (where measure = 5) as invalid,
                            sum(value) filter (where measure = 6) as blank
                     from {facts} where option_id is null and count_round = 0 group by 1, 2
                   )
                   select null::uuid, result_set_id,
                          jsonb_build_object('unit_id', unit_id, 'ballots_cast', ballots_cast, 'valid', valid,
                                             'invalid', invalid, 'blank', blank)
                   from v
                   where ballots_cast is not null and valid is not null
                     and case when invalid is null and blank is null then valid > ballots_cast
                              else valid + coalesce(invalid, 0) + coalesce(blank, 0) <> ballots_cast end"""
            ).format(facts=facts),
        ),
        (
            "accounting.turnout",
            sql.SQL(
                """with v as (
                     select result_set_id, unit_id,
                            sum(value) filter (where measure = 2) as registered,
                            coalesce(sum(value) filter (where measure = 3), sum(value) filter (where measure = 14))
                              as ballots_cast
                     from {facts} where option_id is null and count_round = 0 group by 1, 2
                   )
                   select null::uuid, result_set_id,
                          jsonb_build_object('unit_id', unit_id, 'registered', registered, 'ballots_cast', ballots_cast)
                   from v where ballots_cast > registered"""
            ).format(facts=facts),
        ),
    ]


def _outcomes(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "outcome" not in stage.tables:
        return []
    outcomes = stage.staged("outcome")
    members = (
        [
            (
                "outcomes.list_members",
                sql.SQL(
                    """with m as (
                 select result_set_id, option_id, count(*) as members from {outcomes}
                 where candidacy_id is not null and kind = 'elected' group by 1, 2
               )
               select m.option_id, m.result_set_id, jsonb_build_object('members', m.members, 'seats', w.seats)
               from m join {outcomes} w on w.result_set_id = m.result_set_id and w.option_id = m.option_id
                 and w.candidacy_id is null and w.seats is not null
               where m.members > w.seats"""
                ).format(outcomes=outcomes),
            ),
            (
                "outcomes.member_off_list",
                sql.SQL(
                    """select o.option_id, o.result_set_id, jsonb_build_object('kind', p.kind, 'candidacy', o.candidacy_id)
               from {outcomes} o join {options} p on p.id = o.option_id
               where o.candidacy_id is not null and p.kind not in ('list', 'independents', 'others')"""
                ).format(
                    outcomes=outcomes,
                    options=stage.either(
                        "contest_option",
                        "id",
                        "kind",
                        among=("id", sql.SQL("select distinct option_id from {}").format(outcomes)),
                    ),
                ),
            ),
        ]
        if "candidacy_id" in stage.tables["outcome"]
        else []
    )
    if "result_value" not in stage.tables:
        return members
    result_sets = stage.either(
        "result_set", "id", "contest_id", among=("id", sql.SQL("select result_set_id from {}").format(outcomes))
    )
    return [
        *members,
        (
            "outcomes.winner",
            sql.SQL(
                """with w as materialized (
                     select o.option_id, o.result_set_id, s.rules ->> 'formula' as formula
                     from {outcomes} o
                     join {result_sets} r on r.id = o.result_set_id
                     join {contests} c on c.id = r.contest_id
                     left join {systems} s on s.id = c.electoral_system_id
                     where o.kind in ('elected', 'won') and c.magnitude = 1 and s.rules ->> 'formula' is distinct from 'irv'
                   ),
                   f as materialized (
                     select v.result_set_id, v.option_id, v.value from {facts} v
                     where v.measure = 1 and v.count_round = 0 and v.option_id is not null
                       and v.result_set_id in (select result_set_id from w)
                   ),
                   t as materialized (
                     select f.result_set_id, coalesce(p.parent_option_id, f.option_id) as option_id, sum(f.value) as votes
                     from f left join {options} p on p.id = f.option_id group by 1, 2
                   ),
                   m as materialized (
                     select t.result_set_id, max(t.votes) as top from t join {kinds} p on p.id = t.option_id
                     where p.kind not in ('others', 'independents', 'none_of_the_above', 'uncommitted')
                       and (p.kind <> 'write_in' or exists (select from {candidacies} x where x.option_id = t.option_id))
                       and not exists (select from {outcomes} o where o.result_set_id = t.result_set_id
                                       and o.option_id = t.option_id and o.kind = 'withdrew')
                     group by 1
                   ),
                   n as materialized (select result_set_id, sum(votes) as total from t group by 1)
                   select w.option_id, w.result_set_id, jsonb_build_object('votes', t.votes, 'top', m.top)
                   from w
                   join m on m.result_set_id = w.result_set_id
                   join n on n.result_set_id = w.result_set_id
                   left join t on t.result_set_id = w.result_set_id and t.option_id = w.option_id
                   where t.votes is distinct from m.top
                     and not (w.formula = 'contingent' and m.top * 2 <= n.total)"""
            ).format(
                outcomes=outcomes,
                result_sets=result_sets,
                contests=stage.either(
                    "contest",
                    "id",
                    "magnitude",
                    "electoral_system_id",
                    among=("id", sql.SQL("select contest_id from {}").format(result_sets)),
                ),
                systems=stage.either("electoral_system", "id", "rules"),
                facts=stage.staged("result_value"),
                options=stage.either(
                    "contest_option",
                    "id",
                    "parent_option_id",
                    among=("id", sql.SQL("select distinct option_id from f")),
                ),
                kinds=stage.either(
                    "contest_option", "id", "kind", among=("id", sql.SQL("select distinct option_id from t"))
                ),
                candidacies=stage.either(
                    "candidacy", "id", "option_id", among=("option_id", sql.SQL("select distinct option_id from t"))
                ),
            ),
        ),
    ]


def _dropped(stage: Stage) -> list[tuple[str, sql.Composed]]:
    staged = stage.tables.get("result_set", [])
    if not staged:
        return []

    def column(name: str, fallback: LiteralString) -> sql.Composable:
        if name in staged:
            return sql.SQL("s.{}").format(sql.Identifier(name))
        return sql.SQL("coalesce(p.{}, {})").format(sql.Identifier(name), sql.SQL(fallback))

    outcomes = stage.staged("outcome") if "outcome" in stage.tables else None
    notes = stage.staged("note") if "note" in stage.tables else None
    return [
        (
            "outcome.dropped",
            sql.SQL(
                """select null::uuid, s.id, jsonb_build_object('supersedes_id', {supersedes})
                   from {result_sets} s left join public.result_set p on p.id = s.id
                   where {status} = 'active' and {default} and {supersedes} is not null
                     and (exists (select from public.outcome o where o.result_set_id = {supersedes}){prior})
                     and not exists (select from public.note n where n.entity_id = s.id and n.kind = 'caveat'){noted}
                     and (s.id = any({clears}::uuid[])
                          or not exists (select from public.outcome o where o.result_set_id = s.id)){own}"""
            ).format(
                result_sets=stage.staged("result_set"),
                supersedes=column("supersedes_id", "null"),
                status=column("status", "'active'"),
                default=column("is_default", "false"),
                prior=sql.SQL(" or exists (select from {} o where o.result_set_id = {})").format(
                    outcomes, column("supersedes_id", "null")
                )
                if outcomes is not None
                else sql.SQL(""),
                noted=sql.SQL(
                    " and not exists (select from {} n where n.entity_id = s.id and n.kind = 'caveat')"
                ).format(notes)
                if notes is not None
                else sql.SQL(""),
                own=sql.SQL(" and not exists (select from {} o where o.result_set_id = s.id)").format(outcomes)
                if outcomes is not None
                else sql.SQL(""),
                clears=sql.Literal(stage.clears),
            ),
        )
    ]


def _references(conn: Connection, stage: Stage) -> list[tuple[str, sql.Composed]]:
    queries: list[tuple[str, sql.Composed]] = []
    for table in FACTS:
        for column in stage.tables.get(table, []):
            if column in KEYS:
                queries.append(
                    (
                        "reference.facts",
                        sql.SQL(
                            """select null::uuid, null::uuid,
                                      jsonb_build_object('table', {table}, 'column', {column}, 'rows', count(*))
                               from {facts} s where s.{key} is not null
                                 and not exists (select from {targets} t where t.id = s.{key})
                               having count(*) > 0"""
                        ).format(
                            table=sql.Literal(table),
                            column=sql.Literal(column),
                            facts=stage.staged(table),
                            key=sql.Identifier(column),
                            targets=stage.either(
                                KEYS[column],
                                "id",
                                among=(
                                    "id",
                                    sql.SQL("select distinct {} from {}").format(
                                        sql.Identifier(column), stage.staged(table)
                                    ),
                                ),
                            ),
                        ),
                    )
                )
        if table in stage.tables and "result_set_id" in stage.tables[table]:
            queries.append(
                (
                    "facts.immutable",
                    sql.SQL(
                        """select distinct null::uuid, s.result_set_id, jsonb_build_object('table', {table})
                           from {facts} s join public.result_set p on p.id = s.result_set_id
                           join public.changeset c on c.id = p.changeset_id
                           where c.pipeline is distinct from {pipeline} or {launched}"""
                    ).format(
                        table=sql.Literal(table),
                        facts=stage.staged(table),
                        pipeline=sql.Literal(stage.pipeline),
                        launched=sql.Literal(LAUNCHED_AT is not None),
                    ),
                )
            )
    if "result_set" in stage.tables:
        sizes = dict(
            conn.execute(
                """select p.relname, sum(greatest(c.reltuples, 0))
                   from pg_class p left join pg_inherits i on i.inhparent = p.oid
                   join pg_class c on c.oid = coalesce(i.inhrelid, p.oid)
                   where p.relnamespace = 'public'::regnamespace and p.relname = any(%s)
                   group by 1""",
                [list(FACTS)],
            ).fetchall()
        )
        for table, column, parent in references(conn):
            if table in FACTS and parent != "result_set":
                queries.append(
                    (
                        "reference.stranded",
                        sql.SQL(
                            """with sets as materialized (
                                 select r.id, r.k from {sets} s join public.result_set r on r.id = s.id
                                 where not exists (
                                   select from public.flag f where f.result_set_id = r.id and f.check_code = 'reference.stranded'
                                     and f.details ->> 'table' = {table} and f.details ->> 'column' = {column}){fresh}
                               )
                               select null::uuid, r.id, jsonb_build_object('table', {table}, 'column', {column}, 'rows', x.n)
                               from sets r {stranded}"""
                        ).format(
                            stranded=sql.SQL(
                                """cross join lateral (
                                     select count(*) n from public.{facts} t
                                     where t.result_set_k = r.k and t.{key} <> 0
                                       and not exists (select from public.{parent} p where p.k = t.{key})
                                   ) x where x.n > 0"""
                                if sizes.get(table, 0) > 10_000_000
                                else """join (
                                     select r.id, count(*) n from sets r join public.{facts} t on t.result_set_k = r.k
                                     where t.{key} <> 0 and not exists (select from public.{parent} p where p.k = t.{key})
                                     group by r.id
                                   ) x on x.id = r.id"""
                            ).format(
                                facts=sql.Identifier(table), key=sql.Identifier(column), parent=sql.Identifier(parent)
                            ),
                            table=sql.Literal(table),
                            column=sql.Literal(column),
                            sets=stage.staged("result_set"),
                            fresh=sql.SQL(" and not exists (select from ({}) x where x.k = r.k)").format(
                                stage.replaced(table)
                            )
                            if table in stage.tables
                            else sql.SQL(""),
                        ),
                    )
                )
    return queries


def _geometry(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "area_geometry" not in stage.tables:
        return []
    return [
        (
            "geometry.valid",
            sql.SQL(
                """select area_id, null::uuid, jsonb_build_object('reason', st_isvalidreason(geom))
                   from {geometry} where not st_isvalid(geom)"""
            ).format(geometry=stage.staged("area_geometry")),
        )
    ]


def _elections(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "election" not in stage.tables:
        return []
    return [
        (
            "election.empty",
            sql.SQL(
                """select s.id, null::uuid, jsonb_build_object('date_from', s.date_from)
                   from {elections} s
                   where s.status = 'held'
                     and not exists (select from public.election p where p.id = s.id)
                     and not exists (select from {contests} c where c.election_id = s.id)
                     and not exists (select from {children} e where e.parent_id = s.id)"""
            ).format(
                elections=stage.staged("election"),
                contests=stage.either("contest", "election_id"),
                children=stage.either("election", "parent_id"),
            ),
        ),
        (
            "election.stale_scheduled",
            sql.SQL(
                """select s.id, null::uuid, jsonb_build_object('date_from', s.date_from, 'date_to', s.date_to)
                   from {elections} s
                   where s.status = 'scheduled' and coalesce(s.date_to, s.date_from) < current_date"""
            ).format(elections=stage.staged("election")),
        ),
    ]


def _kinds(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "area_kind" not in stage.tables:
        return []
    return [
        (
            "area.kind_unknown",
            sql.SQL(
                """select k.area_id, null::uuid, jsonb_build_object('kind', k.kind, 'scheme', a.scheme_code)
                   from {kinds} k join {areas} a on a.id = k.area_id
                   where not exists (select from {schemes} s where s.code = a.scheme_code and s.kinds ? k.kind)"""
            ).format(
                kinds=stage.staged("area_kind"),
                areas=stage.either(
                    "area",
                    "id",
                    "scheme_code",
                    among=("id", sql.SQL("select area_id from {}").format(stage.staged("area_kind"))),
                ),
                schemes=stage.either("scheme", "code", "kinds"),
            ),
        )
    ]


def _country_names(stage: Stage) -> list[tuple[str, sql.Composed]]:
    columns = stage.tables.get("name", [])
    if not {"entity_id", "text", "lang", "is_preferred"} <= set(columns):
        return []
    return [
        (
            "name.country_mismatch",
            sql.SQL(
                """with staged as (
                     select distinct on (entity_id, lang) entity_id, lang, text from {name}
                     where is_preferred{timeless}
                     order by entity_id, lang, {english} desc, text
                   ),
                   current as (
                     select entity_id, lang, text from staged
                     union all
                     (select distinct on (n.entity_id, n.lang) n.entity_id, n.lang, n.text from public.name n
                      where n.is_preferred and n.valid_from is null and n.valid_to is null
                        and not exists (select from staged s where s.entity_id = n.entity_id and s.lang = n.lang)
                      order by n.entity_id, n.lang, n.kind = 'english' desc, n.text)
                   ),
                   coded as (
                     select entity_id, value from public.external_id where scheme = 'iso3166'{coded}
                   ),
                   link as (
                     select p.id as polity, c.entity_id as country from public.polity p
                     join coded c on c.value = p.iso_code
                   )
                   select l.polity, null::uuid,
                          jsonb_build_object(
                            'country', l.country, 'lang', a.lang, 'polity_name', a.text, 'country_name', b.text
                          )
                   from link l
                   join current a on a.entity_id = l.polity
                   join current b on b.entity_id = l.country and b.lang = a.lang
                   where a.text <> b.text
                     and (l.polity in (select entity_id from staged) or l.country in (select entity_id from staged))"""
            ).format(
                name=stage.staged("name"),
                timeless=sql.SQL(
                    "".join(f" and {column} is null" for column in ("valid_from", "valid_to") if column in columns)
                ),
                english=sql.SQL("kind = 'english'" if "kind" in columns else "false"),
                coded=sql.SQL(" union select entity_id, value from {} where scheme = 'iso3166'").format(
                    stage.staged("external_id")
                )
                if "external_id" in stage.tables
                else sql.SQL(""),
            ),
        )
    ]


def _seated(stage: Stage) -> list[tuple[str, sql.Composed]]:
    columns = stage.tables.get("tenure", [])
    if not {"body_id", "seat_id", "from_date"} <= set(columns):
        return []
    staged = (
        sql.SQL("left join {} ss on ss.id = t.seat_id").format(stage.staged("seat"))
        if "apportioned_area_id" in stage.tables.get("seat", [])
        else sql.SQL("left join (select null::uuid as id, null::uuid as apportioned_area_id) ss on false")
    )
    return [
        (
            "tenure.placeholder_start",
            sql.SQL(
                """select t.id, null::uuid, jsonb_build_object('from', t.from_date, 'area', g.area_id)
                   from {tenure} t {staged}
                   left join public.seat ps on ps.id = t.seat_id
                   join public.representation_gap g on g.body_id = t.body_id
                     and g.area_id = coalesce(ss.apportioned_area_id, ps.apportioned_area_id) and g.valid @> t.from_date
                   where {precision} extract(year from t.from_date)::int % 2 = 1
                     and to_char(t.from_date, 'MM-DD') in ('03-04', '01-03')"""
            ).format(
                tenure=stage.staged("tenure"),
                staged=staged,
                precision=sql.SQL("t.from_precision = 'day' and" if "from_precision" in columns else ""),
            ),
        )
    ]


def _memberships(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "person_party_membership" not in stage.tables:
        return []
    return [
        (
            "membership.overlap",
            sql.SQL(
                """with m as (
                     select s.person_id, s.party_id, daterange(s.valid_from, s.valid_to) as valid
                     from {staged} s
                   ),
                   o as (
                     select a.person_id, a.party_id, a.valid,
                            b.party_id as other, b.valid as other_valid, a.valid * b.valid as shared
                     from m a join (select person_id, party_id, daterange(valid_from, valid_to) as valid
                                    from {either}) b
                       on b.person_id = a.person_id and b.party_id <> a.party_id and b.valid && a.valid
                   )
                   select o.person_id, null::uuid,
                          jsonb_build_object('party', o.party_id, 'valid', o.valid::text, 'other', o.other,
                                             'other_valid', o.other_valid::text)
                   from o
                   where (lower_inf(o.shared) or upper_inf(o.shared) or upper(o.shared) - lower(o.shared) > 366)
                     and not exists (select from public.party_relation r
                                     where (r.from_party_id, r.to_party_id) in ((o.party_id, o.other), (o.other, o.party_id)))
                     and not exists (select from public.party_affiliation f
                                     where (f.party_id, f.parent_party_id) in ((o.party_id, o.other), (o.other, o.party_id)))"""
            ).format(
                staged=stage.staged("person_party_membership"),
                either=stage.either(
                    "person_party_membership",
                    "person_id",
                    "party_id",
                    "valid_from",
                    "valid_to",
                    among=(
                        "person_id",
                        sql.SQL("select person_id from {}").format(stage.staged("person_party_membership")),
                    ),
                ),
            ),
        )
    ]


def _founding(stage: Stage) -> list[tuple[str, sql.Composed]]:
    founded = """(select p.id as party_id, coalesce(f.date, n.day) as day
                   from public.party p
                   left join public.party_founding f on f.party_id = p.id
                   left join lateral (select min(valid_from) as day from public.name
                                      where entity_id = p.id and valid_from is not null
                                        and not exists (select from public.name o
                                                        where o.entity_id = p.id and o.valid_from is null)) n on true
                   where p.id in (select party_id from {staged}) and coalesce(f.date, n.day) is not null
                     and not exists (select from public.party_relation r
                                     where p.id in (r.from_party_id, r.to_party_id)
                                       and r.kind in ('successor_of', 'refounded_as', 'split_from', 'merged_into')))"""
    queries: list[tuple[str, sql.Composed]] = []
    if {"party_id", "contest_id"} <= set(stage.tables.get("contest_option", [])):
        contests = stage.either(
            "contest",
            "id",
            "election_id",
            among=("id", sql.SQL("select contest_id from {}").format(stage.staged("contest_option"))),
        )
        queries.append(
            (
                "party.before_founding",
                sql.SQL(
                    """select o.id, null::uuid, jsonb_build_object('party', o.party_id, 'founded', f.day, 'election', e.date_from)
                       from {options} o
                       join {contests} c on c.id = o.contest_id
                       join {elections} e on e.id = c.election_id
                       join {founded} f on f.party_id = o.party_id
                       where e.date_from < f.day"""
                ).format(
                    options=stage.staged("contest_option"),
                    contests=contests,
                    elections=stage.either(
                        "election",
                        "id",
                        "date_from",
                        among=("id", sql.SQL("select election_id from {}").format(contests)),
                    ),
                    founded=sql.SQL(founded).format(staged=stage.staged("contest_option")),
                ),
            )
        )
    if {"party_id", "valid_from"} <= set(stage.tables.get("tenure_affiliation", [])):
        queries.append(
            (
                "party.before_founding",
                sql.SQL(
                    """select a.tenure_id, null::uuid, jsonb_build_object('party', a.party_id, 'founded', f.day, 'from', a.valid_from)
                       from {staged} a join {founded} f on f.party_id = a.party_id
                       where a.valid_from < f.day"""
                ).format(
                    staged=stage.staged("tenure_affiliation"),
                    founded=sql.SQL(founded).format(staged=stage.staged("tenure_affiliation")),
                ),
            )
        )
    return queries


def _scope(stage: Stage) -> list[tuple[str, sql.Composed]]:
    queries: list[tuple[str, sql.Composed]] = []
    for table, drop in stage.drops.items():
        queries.append(
            (
                "scope.drops",
                sql.SQL(
                    """select l.id, null::uuid, jsonb_build_object('table', {table}, 'from', {start}::text)
                       from unnest({areas}::uuid[]) l(id)
                       where not exists (select 1 from public.{target} p, changeset c where p.area_id = l.id
                                         and c.id = p.changeset_id and c.pipeline = {pipeline}
                                         and p.valid_from >= {start}::date)"""
                ).format(
                    table=sql.Literal(table),
                    target=sql.Identifier(table),
                    areas=sql.Literal(drop["areas"]),
                    start=sql.Literal(drop["from"]),
                    pipeline=sql.Literal(stage.pipeline),
                ),
            )
        )
    for table, columns in stage.tables.items():
        entity = sql.SQL("s.id" if table in stage.entities and "id" in columns else "null::uuid")
        if "polity_id" in columns and table != "polity_parent":
            queries.append(
                (
                    "scope.polity",
                    sql.SQL(
                        """with recursive scope as (
                             select id from {polities} p where p.iso_code = {polity}
                             union select pp.polity_id from {parents} pp join scope on pp.parent_id = scope.id
                           )
                           select {entity}, null::uuid, jsonb_build_object('table', {table}, 'polity_id', s.polity_id)
                           from {staged} s where s.polity_id not in (select id from scope)"""
                    ).format(
                        polities=stage.either("polity", "id", "iso_code"),
                        parents=stage.either("polity_parent", "polity_id", "parent_id"),
                        polity=sql.Literal(stage.polity),
                        entity=entity,
                        table=sql.Literal(table),
                        staged=stage.staged(table),
                    ),
                )
            )
        compared = [column for column in columns if column != "id"]
        if "id" in columns and compared and table not in FACTS:
            queries.append(
                (
                    "scope.owner",
                    sql.SQL(
                        """select {entity}, null::uuid,
                                  jsonb_build_object('table', {table}, 'id', s.id, 'pipeline', c.pipeline)
                           from {staged} s
                           join public.{target} p on p.id = s.id
                           join changeset c on c.id = p.changeset_id
                           where c.pipeline is distinct from {pipeline} and c.pipeline <> 'contrib'
                             and ({current}) is distinct from ({incoming})"""
                    ).format(
                        entity=entity,
                        table=sql.Literal(table),
                        staged=stage.staged(table),
                        target=sql.Identifier(table),
                        pipeline=sql.Literal(stage.pipeline),
                        current=sql.SQL(", ").join(sql.SQL("p.{}").format(sql.Identifier(c)) for c in compared),
                        incoming=sql.SQL(", ").join(sql.SQL("s.{}").format(sql.Identifier(c)) for c in compared),
                    ),
                )
            )
        if table == "external_id":
            queries.append(
                (
                    "scope.owner",
                    sql.SQL(
                        """select s.entity_id, null::uuid,
                                  jsonb_build_object('table', {table}, 'scheme', s.scheme, 'value', s.value,
                                                     'pipeline', c.pipeline)
                           from {staged} s
                           cross join lateral (
                             select p.changeset_id from public.external_id p
                             where p.scheme = s.scheme and p.value = s.value and p.entity_id <> s.entity_id limit 1
                           ) p
                           join changeset c on c.id = p.changeset_id
                           where c.pipeline is distinct from {pipeline} and c.pipeline <> 'contrib'"""
                    ).format(
                        table=sql.Literal(table), staged=stage.staged(table), pipeline=sql.Literal(stage.pipeline)
                    ),
                )
            )
        if table in stage.temporal and stage.pipeline == "contrib":
            queries.append(
                (
                    "scope.owner",
                    sql.SQL(
                        """select distinct null::uuid, null::uuid,
                                  jsonb_build_object('table', {table}, {key}, p.{column}, 'pipeline', c.pipeline)
                           from public.{target} p
                           join {staged} s on {overlaps}
                           join changeset c on c.id = p.changeset_id
                           where not c.pipeline = any({writers}) and c.pipeline <> 'contrib'"""
                    ).format(
                        table=sql.Literal(table),
                        key=sql.Literal(stage.temporal[table][0]),
                        column=sql.Identifier(stage.temporal[table][0]),
                        target=sql.Identifier(table),
                        staged=stage.staged(table),
                        overlaps=stage.overlaps(table),
                        writers=stage.writers(table),
                    ),
                )
            )
    return [*queries, *overlaps(stage)]


def overlaps(stage: Stage, limit: int | None = None) -> list[tuple[str, sql.Composed]]:
    if stage.pipeline == "contrib":
        return []
    queries: list[tuple[str, sql.Composed]] = []
    for table, keys in stage.temporal.items():
        columns = stage.tables[table]
        values = [
            column
            for column in columns
            if column not in keys and column not in ("valid_from", "valid_to", "geom", "label_point")
        ]
        queries.append(
            (
                "scope.overlap",
                sql.SQL(
                    """select null::uuid, null::uuid,
                              jsonb_build_object('table', {table}, 'key', jsonb_build_object({keys}),
                                                 'staged', jsonb_build_object({staged}'valid', {start}),
                                                 'published', o.rows)
                       from {source} s
                       cross join lateral (
                         select jsonb_agg(jsonb_build_object({published}'valid', p.valid::text,
                                                             'pipeline', c.pipeline, 'changeset_id', c.id)
                                          order by p.valid) as rows
                         from public.{target} p join changeset c on c.id = p.changeset_id
                         where {overlaps} and not c.pipeline = any({writers}) and c.pipeline <> 'contrib'
                       ) o
                       where o.rows is not null
                         and not exists (select from public.{target} p join changeset c on c.id = p.changeset_id
                                         where c.pipeline = 'contrib' and {overlaps})
                       order by 3{limit}"""
                ).format(
                    table=sql.Literal(table),
                    keys=sql.SQL(", ").join(
                        sql.SQL("{}, s.{}").format(sql.Literal(key), sql.Identifier(key)) for key in keys
                    ),
                    staged=sql.SQL("").join(
                        sql.SQL("{}, s.{}, ").format(sql.Literal(column), sql.Identifier(column)) for column in values
                    ),
                    published=sql.SQL("").join(
                        sql.SQL("{}, p.{}, ").format(sql.Literal(column), sql.Identifier(column)) for column in values
                    ),
                    start=sql.SQL("daterange({}, {}, '[)')::text").format(
                        sql.SQL("s.valid_from" if "valid_from" in columns else "null::date"),
                        sql.SQL("s.valid_to" if "valid_to" in columns else "null::date"),
                    ),
                    source=stage.staged(table),
                    target=sql.Identifier(table),
                    overlaps=stage.overlaps(table),
                    writers=stage.writers(table),
                    limit=sql.SQL("") if limit is None else sql.SQL(" limit {}").format(sql.Literal(limit)),
                ),
            )
        )
    return queries


def _capitals(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "is_preferred" not in stage.tables.get("name", []):
        return []
    return [
        (
            "name.capitals",
            sql.SQL(
                """select n.entity_id, null::uuid, jsonb_build_object('text', n.text, 'lang', n.lang)
                   from {names} n join {entities} e on e.id = n.entity_id and e.type = 'person'
                   where n.is_preferred and exists (
                     select from regexp_matches(n.text, '[[:alpha:]]+', 'g') w
                     where w[1] ~ '^[[:upper:]]{{3,}}$' and w[1] <> all({known}))"""
            ).format(
                names=stage.staged("name"),
                entities=stage.either(
                    "entity",
                    "id",
                    "type",
                    among=("id", sql.SQL("select entity_id from {}").format(stage.staged("name"))),
                ),
                known=sql.Literal(sorted(ACRONYMS | ROMAN)),
            ),
        )
    ]


def _held_translations(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "translation" not in stage.tables:
        return []
    return [
        (
            "translation.held",
            sql.SQL(
                """select null::uuid, null::uuid,
                          jsonb_build_object('source_md5', s.source_md5, 'lang', s.lang, 'held_origin', p.origin)
                   from {} s join public.translation p using (source_md5, lang)
                   where s.origin = 'machine' and p.origin <> 'machine'"""
            ).format(stage.staged("translation")),
        )
    ]


def _cadence(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "election_cadence" not in stage.tables:
        return []
    staged = stage.staged("election_cadence")
    columns = stage.tables["election_cadence"]
    kinds: dict[str, LiteralString] = {
        "government_kind": "text",
        "function": "text",
        "name_pattern": "text",
        "body_id": "uuid",
        "office_id": "uuid",
        "valid_from": "date",
        "valid_to": "date",
        "source_id": "uuid",
        "locator": "text",
    }
    held = {
        column: sql.Identifier(column) if column in columns else sql.SQL("null::" + kind)
        for column, kind in kinds.items()
    }
    rules = sql.SQL(
        """(select id, polity_id, {government_kind} as government_kind, {function} as function,
                   {name_pattern} as name_pattern, {body_id} as body_id, {office_id} as office_id,
                   daterange({valid_from}, {valid_to}, '[)') as valid
            from {staged}
            union all
            select p.id, p.polity_id, p.government_kind, p.function, p.name_pattern, p.body_id, p.office_id,
                   daterange(p.valid_from, p.valid_to, '[)')
            from public.election_cadence p join changeset c on c.id = p.changeset_id
            where not exists (select from {staged} s where s.id = p.id){swept})"""
    ).format(
        staged=staged,
        swept=sql.SQL(" and c.pipeline <> {}").format(sql.Literal(stage.pipeline))
        if "election_cadence" in stage.sweeps
        else sql.SQL(""),
        **held,
    )
    return [
        (
            "cadence.uncited",
            sql.SQL(
                """select id, null::uuid, jsonb_build_object('polity', polity_id, 'kind', {government_kind})
                   from {staged} where {source_id} is null or coalesce(btrim({locator}), '') = ''"""
            ).format(staged=staged, **held),
        ),
        (
            "cadence.overlap",
            sql.SQL(
                """select a.id, null::uuid, jsonb_build_object('other', b.id, 'polity', a.polity_id, 'kind', a.government_kind,
                          'function', a.function, 'name_pattern', a.name_pattern)
                   from {staged} s join {rules} a on a.id = s.id
                   join {rules} b on b.id <> a.id and b.polity_id = a.polity_id
                     and b.government_kind is not distinct from a.government_kind
                     and b.function is not distinct from a.function
                     and b.name_pattern is not distinct from a.name_pattern
                     and b.body_id is not distinct from a.body_id
                     and b.office_id is not distinct from a.office_id
                     and b.valid && a.valid"""
            ).format(staged=staged, rules=rules),
        ),
    ]


def _figures(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "area_figure" not in stage.tables:
        return []
    staged = stage.staged("area_figure")
    return [
        (
            "figure.duplicate",
            sql.SQL(
                """select s.area_id, null::uuid,
                          jsonb_build_object('kind', s.kind, 'date', s.date, 'source', s.source_id,
                                             'values', array_agg(distinct s.value order by s.value))
                   from {staged} s group by s.area_id, s.kind, s.date, s.source_id
                   having count(distinct s.value) > 1"""
            ).format(staged=staged),
        ),
        (
            "figure.value",
            sql.SQL(
                """select s.area_id, null::uuid, jsonb_build_object('kind', s.kind, 'date', s.date, 'value', s.value)
                   from {staged} s where s.value is null or s.value <= 0"""
            ).format(staged=staged),
        ),
        (
            "figure.area_absent",
            sql.SQL(
                """select distinct s.area_id, null::uuid, jsonb_build_object('kind', s.kind, 'date', s.date)
                   from {staged} s
                   where (exists (select from {parents} p where p.area_id = s.area_id)
                          and not exists (select from {parents} p where p.area_id = s.area_id
                                          and daterange(p.valid_from, p.valid_to, '[)') @> s.date))
                      or exists (select from {members} m join {events} e on e.id = m.event_id
                                 where m.area_id = s.area_id and e.date is not null
                                   and ((e.kind = 'created' and m.role = 'to' and e.date > s.date)
                                        or (e.kind = 'dissolved' and m.role = 'from' and e.date <= s.date)))"""
            ).format(
                staged=staged,
                parents=stage.either("area_parent", "area_id", "valid_from", "valid_to"),
                members=stage.either("area_event_member", "event_id", "area_id", "role"),
                events=stage.either("area_event", "id", "kind", "date"),
            ),
        ),
    ]


CENSUS_SELECTORS: tuple[LiteralString, ...] = (
    "election_kinds",
    "contest_kinds",
    "body_kinds",
    "office_kinds",
    "polity_kinds",
    "within_polity_id",
    "iso_pattern",
    "schemes",
    "except_codes",
    "name_pattern",
    "body_id",
    "office_id",
)


def _live(stage: Stage, table: str, *columns: LiteralString) -> sql.Composable:
    staged = sql.SQL(", ").join(
        sql.Identifier(column)
        if column in stage.tables[table]
        else sql.SQL("null as {}").format(sql.Identifier(column))
        for column in columns
    )
    return sql.SQL(
        """(select true as staged, {staged} from {table}
            union all
            select false, {public} from public.{name} p join changeset c on c.id = p.changeset_id
            where not exists (select from {table} x where x.id = p.id){swept})"""
    ).format(
        staged=staged,
        table=stage.staged(table),
        public=sql.SQL(", ").join(sql.SQL("p.{}").format(sql.Identifier(column)) for column in columns),
        name=sql.Identifier(table),
        swept=sql.SQL(" and c.pipeline <> {}").format(sql.Literal(stage.pipeline))
        if table in stage.sweeps
        else sql.SQL(""),
    )


def _census(stage: Stage) -> list[tuple[str, sql.Composed]]:
    found: list[tuple[str, sql.Composed]] = []
    if "census_tier_count" in stage.tables:
        found.append(
            (
                "census.count_source",
                sql.SQL(
                    """select s.id, null::uuid, jsonb_build_object('tier', s.tier_id, 'source', s.source_id, 'kind', o.kind)
                       from {staged} s join {sources} o on o.id = s.source_id
                       where o.kind <> 'official_body'"""
                ).format(staged=stage.staged("census_tier_count"), sources=stage.either("source", "id", "kind")),
            )
        )
    if "census_tier" in stage.tables:
        tiers = _live(stage, "census_tier", "id", "polity_id", "parent_tier_id")
        found += [
            (
                "census.tier_parent",
                sql.SQL(
                    """select t.id, null::uuid, jsonb_build_object('parent', t.parent_tier_id, 'polity', t.polity_id,
                                                                 'parent_polity', p.polity_id)
                       from {tiers} t left join {tiers} p on p.id = t.parent_tier_id
                       where t.staged and t.parent_tier_id is not null
                         and p.polity_id is distinct from t.polity_id"""
                ).format(tiers=tiers),
            ),
            (
                "census.tier_cycle",
                sql.SQL(
                    """with recursive t as (select * from {tiers}),
                       up (start, id, path) as (
                         select t.id, t.parent_tier_id, array[t.id] from t where t.staged and t.parent_tier_id is not null
                         union all
                         select up.start, t.parent_tier_id, up.path || up.id
                         from up join t on t.id = up.id
                         where t.parent_tier_id is not null and not up.id = any(up.path))
                       select distinct start, null::uuid, jsonb_build_object('path', path)
                       from up where id = start"""
                ).format(tiers=tiers),
            ),
        ]
    if "census_class" in stage.tables:
        found.append(
            (
                "census.class_tier",
                sql.SQL(
                    """select s.id, null::uuid, jsonb_build_object('key', s.key, 'tier', s.tier_id, 'polity', s.polity_id,
                                                                 'tier_polity', t.polity_id)
                       from {staged} s left join {tiers} t on t.id = s.tier_id
                       where t.polity_id is distinct from s.polity_id"""
                ).format(staged=stage.staged("census_class"), tiers=stage.either("census_tier", "id", "polity_id")),
            )
        )
    if "census_match" in stage.tables:
        matches = _live(
            stage, "census_match", "id", "class_id", "precedence", "valid_from", "valid_to", *CENSUS_SELECTORS
        )
        classes = stage.either("census_class", "id", "polity_id")
        found.append(
            (
                "census.match_overlap",
                sql.SQL(
                    """select a.id, null::uuid, jsonb_build_object('class', a.class_id, 'other', b.id, 'other_class', b.class_id)
                       from {matches} a
                       join {classes} ca on ca.id = a.class_id
                       join {matches} b on b.class_id <> a.class_id and (a.staged or b.staged)
                         and (not b.staged or b.id::text > a.id::text)
                         and coalesce(a.precedence, 0) = coalesce(b.precedence, 0)
                         and daterange(a.valid_from, a.valid_to, '[)') && daterange(b.valid_from, b.valid_to, '[)')
                         and {same}
                       join {classes} cb on cb.id = b.class_id and cb.polity_id = ca.polity_id
                       where a.staged"""
                ).format(
                    matches=matches,
                    classes=classes,
                    same=sql.SQL(" and ").join(
                        sql.SQL("a.{0} is not distinct from b.{0}").format(sql.Identifier(column))
                        for column in CENSUS_SELECTORS
                    ),
                ),
            )
        )
    if "census_date" in stage.tables:
        dates = _live(
            stage,
            "census_date",
            "id",
            "class_id",
            "within_polity_id",
            "date",
            "date_precision",
            "through",
            "election_kind",
        )
        found += [
            (
                "census.date_duplicate",
                sql.SQL(
                    """select a.id, null::uuid, jsonb_build_object('class', a.class_id, 'date', a.date, 'other', b.id)
                       from {dates} a join {dates} b on b.id <> a.id and b.class_id = a.class_id
                         and b.within_polity_id is not distinct from a.within_polity_id
                         and b.date = a.date and b.election_kind is not distinct from a.election_kind
                         and (not b.staged or b.id::text > a.id::text)
                       where a.staged"""
                ).format(dates=dates),
            ),
            (
                "census.date_outside",
                sql.SQL(
                    """select s.id, null::uuid, jsonb_build_object('class', s.class_id, 'date', s.date,
                                                                 'valid_from', k.valid_from, 'valid_to', k.valid_to)
                       from {dates} s join {classes} k on k.id = s.class_id
                       where s.staged and not daterange(k.valid_from, k.valid_to, '[)') && daterange(
                         s.date,
                         (coalesce(s.through, s.date) + case coalesce(s.date_precision::text, 'day')
                           when 'year' then interval '1 year' when 'month' then interval '1 month' else interval '1 day' end)::date,
                         '[)')"""
                ).format(dates=dates, classes=stage.either("census_class", "id", "valid_from", "valid_to")),
            ),
        ]
    return found


def _composition(stage: Stage) -> list[tuple[str, sql.Composed]]:
    if "body_composition" not in stage.tables and "body_composition_party" not in stage.tables:
        return []
    compositions = stage.either("body_composition", "id", "body_id", "as_of", "seats", "control")
    parties = (
        _live(stage, "body_composition_party", "id", "composition_id", "seats", "role")
        if "body_composition_party" in stage.tables
        else stage.either("body_composition_party", "id", "composition_id", "seats", "role")
    )
    touched = sql.SQL(" union ").join(
        sql.SQL("select {} from {}").format(sql.Identifier(column), stage.staged(table))
        for table, column in (("body_composition", "id"), ("body_composition_party", "composition_id"))
        if table in stage.tables
    )
    return [
        (
            "composition.seats",
            sql.SQL(
                """select c.body_id, null::uuid, jsonb_build_object('composition', c.id, 'seats', c.seats,
                                                             'counted', coalesce(sum(p.seats), 0))
                   from {compositions} c left join {parties} p on p.composition_id = c.id
                   where c.id in ({touched})
                   group by c.id, c.body_id, c.seats having coalesce(sum(p.seats), 0) <> c.seats"""
            ).format(compositions=compositions, parties=parties, touched=touched),
        ),
        (
            "composition.size",
            sql.SQL(
                """select c.body_id, null::uuid, jsonb_build_object('composition', c.id, 'as_of', c.as_of, 'seats', c.seats,
                                                             'body_size', z.seats)
                   from {compositions} c join {sizes} z on z.body_id = c.body_id
                     and daterange(z.valid_from, z.valid_to, '[)') @> c.as_of
                   where c.id in ({touched}) and z.seats <> c.seats"""
            ).format(
                compositions=compositions,
                sizes=stage.either("body_size", "body_id", "seats", "valid_from", "valid_to"),
                touched=touched,
            ),
        ),
        (
            "composition.role",
            sql.SQL(
                """select distinct c.body_id, null::uuid, jsonb_build_object('composition', c.id, 'control', c.control)
                   from {compositions} c join {parties} p on p.composition_id = c.id
                   where c.id in ({touched}) and p.role is not null and (c.control is null or c.control = 'noc')"""
            ).format(compositions=compositions, parties=parties, touched=touched),
        ),
    ]


def queries(conn: Connection, stage: Stage) -> list[tuple[str, sql.Composed]]:
    return [
        *_references(conn, stage),
        *_accounting(stage),
        *_outcomes(stage),
        *_dropped(stage),
        *_geometry(stage),
        *_elections(stage),
        *_kinds(stage),
        *_country_names(stage),
        *_seated(stage),
        *_memberships(stage),
        *_founding(stage),
        *_scope(stage),
        *_capitals(stage),
        *_held_translations(stage),
        *_cadence(stage),
        *_figures(stage),
        *_census(stage),
        *_composition(stage),
    ]


def run(conn: Connection, stage: Stage) -> list[Finding]:
    findings: list[Finding] = []
    if "electoral_system" in stage.tables:
        for system, rules in conn.execute(sql.SQL("select id, rules from {}").format(stage.staged("electoral_system"))):
            error = best_match(RULES.iter_errors(rules))
            if error is not None:
                findings.append(
                    Finding(
                        "rules.schema",
                        "error",
                        system,
                        None,
                        {"path": list(error.absolute_path), "message": error.message},
                    )
                )
    changeset = UUID(stage.schema.removeprefix("cs_"))
    (previous,) = conn.execute("select current_setting('statement_timeout')").fetchone() or ("0",)
    for code, query in queries(conn, stage):
        conn.execute("select set_config('statement_timeout', %s, true)", [TIMEOUT])
        started = time.monotonic()
        try:
            rows = conn.execute(query).fetchall()
        except errors.QueryCanceled as error:
            raise TimeoutError(f"check {code} on changeset {changeset} ran over {TIMEOUT}") from error
        finally:
            print(f"check {code}: {time.monotonic() - started:.1f}s", file=sys.stderr, flush=True)
        conn.execute("select set_config('statement_timeout', %s, true)", [previous])
        findings.extend(
            Finding(
                code,
                "warning"
                if code
                in (
                    "membership.overlap",
                    "party.before_founding",
                    "name.capitals",
                    "translation.held",
                    "election.empty",
                    "election.stale_scheduled",
                    "composition.size",
                )
                else "error",
                entity_id,
                result_set_id,
                details,
            )
            for entity_id, result_set_id, details in rows
        )
    return findings


def merged(
    conn: Connection, changeset_id: str | None = None, tables: set[str] | None = None
) -> list[tuple[str, str, UUID, UUID, UUID, int]]:
    types = [
        kind
        for (kind,) in conn.execute(
            "select distinct e.type::text from entity_merge m join entity e on e.id = m.entity_id"
        ).fetchall()
    ]
    found: list[tuple[str, str, UUID, UUID, UUID, int]] = []
    for relation, column, stamped in conn.execute(
        """select c.conrelid::regclass::text, a.attname, exists (
                  select from pg_attribute s where s.attrelid = c.conrelid and s.attname = 'changeset_id'
                    and not s.attisdropped)
           from pg_constraint c join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
           where c.contype = 'f' and c.connamespace = 'public'::regnamespace
             and c.confrelid::regclass::text = any(%s)
             and c.conrelid::regclass::text not in ('search_document', 'entity_merge')
             and not exists (
               select from pg_constraint k where k.conrelid = c.conrelid and k.contype = 'p'
                 and a.attnum = any(k.conkey))""",
        [["entity", *types]],
    ).fetchall():
        if changeset_id is not None and not stamped or tables is not None and relation not in tables:
            continue
        found.extend(
            conn.execute(
                sql.SQL(
                    """select {relation}, {column}, m.entity_id, m.into_id, m.changeset_id, count(*)::int
                       from public.{table} t join entity_merge m on m.entity_id = t.{key} {scope}
                       group by 3, 4, 5"""
                ).format(
                    relation=sql.Literal(relation),
                    column=sql.Literal(column),
                    table=sql.Identifier(relation),
                    key=sql.Identifier(column),
                    scope=sql.SQL("where t.changeset_id = {}").format(sql.Literal(changeset_id))
                    if changeset_id is not None
                    else sql.SQL(""),
                )
            ).fetchall()
        )
    for relation, column, parent in references(conn):
        if parent not in types or tables is not None and relation not in tables:
            continue
        found.extend(
            conn.execute(
                sql.SQL(
                    """select {relation}, {column}, m.entity_id, m.into_id, m.changeset_id, count(*)::int
                       from public.{table} t join public.{parent} p on p.k = t.{key}
                       join entity_merge m on m.entity_id = p.id {scope}
                       group by 3, 4, 5"""
                ).format(
                    relation=sql.Literal(relation),
                    column=sql.Literal(column),
                    table=sql.Identifier(relation),
                    parent=sql.Identifier(parent),
                    key=sql.Identifier(column),
                    scope=sql.SQL(
                        "where t.result_set_k in (select k from public.result_set where changeset_id = {})"
                    ).format(sql.Literal(changeset_id))
                    if changeset_id is not None
                    else sql.SQL(""),
                )
            ).fetchall()
        )
    return found
