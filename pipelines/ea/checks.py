import json
from dataclasses import dataclass, field
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
REPLACEABLE = ("area_geometry", "area_parent", "tenure_affiliation")
DROPPABLE = ("area_code", "area_parent", "area_geometry")
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

    def __post_init__(self) -> None:
        if unknown := sorted(set(self.drops) - set(DROPPABLE)):
            raise ValueError(f"scope.drops may name only {', '.join(DROPPABLE)}, not {', '.join(unknown)}")
        if unstaged := sorted(set(self.drops) - set(self.tables)):
            raise ValueError(f"scope.drops names {', '.join(unstaged)}, which the changeset does not stage")
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
               and not exists (select from {staged} x where {same})"""
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
    contests = stage.either("contest", "id", "magnitude")
    result_sets = stage.either("result_set", "id", "contest_id")
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
                       and (p.kind <> 'write_in' or exists (select from {candidacies} x where x.option_id = t.option_id))
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
                           from {facts} s join public.result_set p on p.id = s.result_set_id
                           join public.changeset c on c.id = p.changeset_id
                           where c.pipeline is distinct from {pipeline}"""
                    ).format(table=sql.Literal(table), facts=stage.staged(table), pipeline=sql.Literal(stage.pipeline)),
                )
            )
    if "result_set" in stage.tables:
        for table, column, parent in references(conn):
            if table in FACTS and parent != "result_set":
                queries.append(
                    (
                        "reference.stranded",
                        sql.SQL(
                            """select null::uuid, r.id, jsonb_build_object('table', {table}, 'column', {column}, 'rows', count(*))
                               from {sets} s join public.result_set r on r.id = s.id
                               join public.{facts} t on t.result_set_k = r.k
                               where t.{key} <> 0 and not exists (select from public.{parent} p where p.k = t.{key}){fresh}
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
                either=stage.either("person_party_membership", "person_id", "party_id", "valid_from", "valid_to"),
            ),
        )
    ]


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
                           where not c.pipeline = any({writers})"""
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
    for code, query in [
        *_references(conn, stage),
        *_accounting(stage),
        *_outcomes(stage),
        *_geometry(stage),
        *_country_names(stage),
        *_seated(stage),
        *_memberships(stage),
        *_scope(stage),
    ]:
        findings.extend(
            Finding(code, "warning" if code == "membership.overlap" else "error", entity_id, result_set_id, details)
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
