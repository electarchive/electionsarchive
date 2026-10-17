import json
from dataclasses import dataclass
from typing import LiteralString
from uuid import UUID

from jsonschema import Draft7Validator
from jsonschema.exceptions import best_match
from psycopg import sql

from ea.store import ROOT, Connection

FACTS = ("result_value", "result_flow", "value_locator")
KEYS = {
    "result_set_id": "result_set",
    "unit_id": "reporting_unit",
    "option_id": "contest_option",
    "from_option_id": "contest_option",
}
REFERENCES = (
    *((fact, key, KEYS[f"{key.removesuffix('_k')}_id"]) for fact in FACTS for key in ("unit_k", "option_k")),
    ("result_flow", "from_option_k", "contest_option"),
    ("result_rollup", "option_k", "contest_option"),
    ("result_rollup", "area_k", "area"),
)
RULES = Draft7Validator(json.loads((ROOT / "packages" / "engine" / "rules.schema.json").read_text()))


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

    def staged(self, table: str) -> sql.Composable:
        return sql.SQL("{}.{}").format(sql.Identifier(self.schema), sql.Identifier(table))

    def overlaps(self, table: str) -> sql.Composable:
        columns = self.tables[table]
        return sql.SQL(
            """{keys} and p.valid && daterange({start}, {to}, '[)')
               and not exists (select 1 from {staged} x where {same})"""
        ).format(
            keys=sql.SQL(" and ").join(
                sql.SQL("p.{0} = s.{0}").format(sql.Identifier(key)) for key in self.temporal[table]
            ),
            start=sql.SQL("s.valid_from" if "valid_from" in columns else "null::date"),
            to=sql.SQL("s.valid_to" if "valid_to" in columns else "null::date"),
            staged=self.staged(table),
            same=sql.SQL(" and ").join(
                sql.SQL(
                    "x.{0} = p.{0}" if column in self.temporal[table] else "x.{0} is not distinct from p.{0}"
                ).format(sql.Identifier(column))
                for column in columns
            ),
        )

    def stale(self, table: str) -> sql.Composable:
        parent = sql.Identifier(self.parents[table])
        return sql.SQL(
            """c.id = p.changeset_id and c.pipeline = {pipeline}
               and p.{parent} in (select {parent} from {staged})
               and not exists (select from {staged} x where {same})"""
        ).format(
            pipeline=sql.Literal(self.pipeline),
            parent=parent,
            staged=self.staged(table),
            same=sql.SQL(" and ").join(
                sql.SQL("x.{0} is not distinct from p.{0}").format(sql.Identifier(column))
                for column in self.tables[table]
            ),
        )

    def either(self, table: str, *columns: LiteralString) -> sql.Composable:
        listed = sql.SQL(", ").join(map(sql.Identifier, columns))
        public = sql.SQL("select {} from public.{} p").format(listed, sql.Identifier(table))
        if table not in self.tables:
            return sql.SQL("({})").format(public)
        if columns[0] == "id":
            public = sql.SQL("{} where not exists (select from {} x where x.id = p.id)").format(
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
    if "result_value" not in stage.tables:
        return []
    facts = stage.staged("result_value")
    contests = stage.either("contest", "id", "magnitude")
    result_sets = stage.either("result_set", "id", "contest_id")
    return [
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
                ).format(outcomes=stage.staged("outcome")),
            )
        ]
        if "candidacy_id" in stage.tables["outcome"]
        else []
    )
    if "result_value" not in stage.tables:
        return members
    return [
        *members,
        (
            "outcomes.winner",
            sql.SQL(
                """with t as (
                     select v.result_set_id, coalesce(p.parent_option_id, v.option_id) as option_id,
                            sum(v.value) as votes
                     from {facts} v left join {options} p on p.id = v.option_id
                     where v.measure = 1 and v.count_round = 0 and v.option_id is not null group by 1, 2
                   ),
                   m as (
                     select t.result_set_id, max(t.votes) as top from t join {options} p on p.id = t.option_id
                     where p.kind not in ('others', 'none_of_the_above', 'uncommitted')
                       and (p.kind <> 'write_in' or exists (select from {candidacies} x where x.option_id = p.id))
                       and not exists (select from {outcomes} w where w.result_set_id = t.result_set_id
                                       and w.option_id = t.option_id and w.kind = 'withdrew')
                     group by 1
                   )
                   select o.option_id, o.result_set_id, jsonb_build_object('votes', t.votes, 'top', m.top)
                   from {outcomes} o
                   join {result_sets} r on r.id = o.result_set_id
                   join {contests} c on c.id = r.contest_id
                   left join {systems} s on s.id = c.electoral_system_id
                   join m on m.result_set_id = o.result_set_id
                   left join t on t.result_set_id = o.result_set_id and t.option_id = o.option_id
                   where o.kind in ('elected', 'won') and c.magnitude = 1 and t.votes is distinct from m.top
                     and s.rules ->> 'formula' is distinct from 'irv'"""
            ).format(
                facts=stage.staged("result_value"),
                options=stage.either("contest_option", "id", "parent_option_id", "kind"),
                candidacies=stage.either("candidacy", "id", "option_id"),
                outcomes=stage.staged("outcome"),
                result_sets=stage.either("result_set", "id", "contest_id"),
                contests=stage.either("contest", "id", "magnitude", "electoral_system_id"),
                systems=stage.either("electoral_system", "id", "rules"),
            ),
        ),
    ]


def _references(stage: Stage) -> list[tuple[str, sql.Composed]]:
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
                            targets=stage.either(KEYS[column], "id"),
                        ),
                    )
                )
        if table in stage.tables and "result_set_id" in stage.tables[table]:
            queries.append(
                (
                    "facts.immutable",
                    sql.SQL(
                        """select distinct null::uuid, s.result_set_id, jsonb_build_object('table', {table})
                           from {facts} s join public.result_set p on p.id = s.result_set_id"""
                    ).format(table=sql.Literal(table), facts=stage.staged(table)),
                )
            )
    if "result_set" in stage.tables:
        for table, column, parent in REFERENCES:
            if table in FACTS:
                queries.append(
                    (
                        "reference.stranded",
                        sql.SQL(
                            """select null::uuid, r.id, jsonb_build_object('table', {table}, 'column', {column}, 'rows', count(*))
                               from {sets} s join public.result_set r on r.id = s.id
                               join public.{facts} t on t.result_set_k = r.k
                               where t.{key} <> 0 and not exists (select from public.{parent} p where p.k = t.{key})
                                 and not exists (
                                   select from public.flag f where f.result_set_id = r.id and f.check_code = 'reference.stranded'
                                     and f.details ->> 'table' = {table} and f.details ->> 'column' = {column})
                               group by r.id"""
                        ).format(
                            table=sql.Literal(table),
                            column=sql.Literal(column),
                            sets=stage.staged("result_set"),
                            facts=sql.Identifier(table),
                            key=sql.Identifier(column),
                            parent=sql.Identifier(parent),
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


def _scope(stage: Stage) -> list[tuple[str, sql.Composed]]:
    queries: list[tuple[str, sql.Composed]] = []
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
                           where c.pipeline is distinct from {pipeline} and ({current}) is distinct from ({incoming})"""
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
                           join public.external_id p using (scheme, value)
                           join changeset c on c.id = p.changeset_id
                           where c.pipeline is distinct from {pipeline} and p.entity_id <> s.entity_id"""
                    ).format(
                        table=sql.Literal(table), staged=stage.staged(table), pipeline=sql.Literal(stage.pipeline)
                    ),
                )
            )
        if table in stage.temporal:
            queries.append(
                (
                    "scope.owner",
                    sql.SQL(
                        """select distinct null::uuid, null::uuid,
                                  jsonb_build_object('table', {table}, {key}, p.{column}, 'pipeline', c.pipeline)
                           from public.{target} p
                           join {staged} s on {overlaps}
                           join changeset c on c.id = p.changeset_id
                           where c.pipeline is distinct from {pipeline}"""
                    ).format(
                        table=sql.Literal(table),
                        key=sql.Literal(stage.temporal[table][0]),
                        column=sql.Identifier(stage.temporal[table][0]),
                        target=sql.Identifier(table),
                        staged=stage.staged(table),
                        overlaps=stage.overlaps(table),
                        pipeline=sql.Literal(stage.pipeline),
                    ),
                )
            )
    return queries


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
    for code, query in [*_references(stage), *_accounting(stage), *_outcomes(stage), *_geometry(stage), *_scope(stage)]:
        findings.extend(
            Finding(code, "error", entity_id, result_set_id, details)
            for entity_id, result_set_id, details in conn.execute(query).fetchall()
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
    for relation, column, parent in REFERENCES:
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
