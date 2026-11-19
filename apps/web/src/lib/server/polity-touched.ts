import type { SQL } from "drizzle-orm";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";

function changed(table: string, columns: string) {
  return sql.raw(`
    select ${columns}, cs.at from ${table} t join cs on cs.id = t.changeset_id
    union all
    select ${columns}, cs.at from ${table}_history t join cs on cs.id = t.superseded_by
  `);
}

export function touched(changesets: SQL, records: SQL) {
  return sql`
    with recursive cs(id, at) as materialized (${changesets}),
    rec as materialized (select polity_id as id, record from (${records}) r),
    bare as materialized (
      select r.id from rec r join polity p on p.id = r.id
      where p.kind = 'region' or (p.kind = 'federal_unit'
        and not exists (select from body where polity_id = p.id)
        and not exists (select from office where polity_id = p.id))
    ),
    anc(id, up) as (
      select b.id, b.id from bare b
      union
      select anc.id, pp.parent_id from anc join polity_parent pp on pp.polity_id = anc.up
    ),
    keep as materialized (
      select id, id as up from rec union select id, up from anc
    ),
    opt as materialized (
      select t.option_id as id, max(t.at) as at from (
        ${changed("outcome", "t.option_id")}
        union all
        ${changed("candidacy", "t.option_id")}
      ) t group by t.option_id
    ),
    ct as materialized (
      select t.contest_id as id, max(t.at) as at from (
        ${changed("result_set", "t.contest_id")}
        union all
        ${changed("contest_option", "t.contest_id")}
        union all
        select q.contest_id, o.at from opt o join contest_option q on q.id = o.id
      ) t group by t.contest_id
    ),
    ent as materialized (
      select t.id, max(t.at) as at from (
        ${changed("name", "t.entity_id as id")}
        union all
        ${changed("slug", "t.entity_id as id")}
        union all
        ${changed("entity_merge", "t.entity_id as id")}
        union all
        ${changed("entity_retirement", "t.entity_id as id")}
      ) t group by t.id
    ),
    hit(id, at) as (
      ${changed("election", "t.polity_id")}
      union all
      ${changed("body", "t.polity_id")}
      union all
      ${changed("office", "t.polity_id")}
      union all
      ${changed("government", "t.polity_id")}
      union all
      ${changed("electoral_system", "t.polity_id")}
      union all
      ${changed("polity", "t.id")}
      union all
      ${changed("polity_parent", "t.polity_id")}
      union all
      ${changed("polity_territory", "t.polity_id")}
      union all
      select coalesce(b.polity_id, f.polity_id), h.at from (
        ${changed("seat", "t.body_id, null::uuid as office_id")}
        union all
        ${changed("body_size", "t.body_id, null::uuid as office_id")}
        union all
        ${changed("tenure", "t.body_id, t.office_id")}
      ) h left join body b on b.id = h.body_id left join office f on f.id = h.office_id
      union all
      select x.polity_id, h.at from (${changed("alliance", "t.election_id")}) h join election x on x.id = h.election_id
      union all
      select v.id, h.at from (
        ${changed("contest", "t.election_id, t.body_id, t.office_id")}
        union all
        select c.election_id, c.body_id, c.office_id, t.at from ct t join contest c on c.id = t.id
      ) h
        left join election x on x.id = h.election_id
        left join body b on b.id = h.body_id
        left join office f on f.id = h.office_id
        cross join lateral (values (x.polity_id), (b.polity_id), (f.polity_id)) v(id)
      union all
      select v.id, n.at from ent n
        left join election x on x.id = n.id
        left join body b on b.id = n.id
        left join office f on f.id = n.id
        cross join lateral (values (x.polity_id), (b.polity_id), (f.polity_id), (n.id)) v(id)
    ),
    parties as materialized (
      select t.id, max(t.at) as at from (
        ${changed("party", "t.id")}
        union all
        select id, at from ent
      ) t join party p on p.id = t.id group by t.id
    )
    select 'polity' as kind, k.id::text as id, (extract(epoch from max(h.at)) * 1000000)::int8::text as at
    from hit h join keep k on k.up = h.id group by k.id
    union all
    select 'party', id::text, (extract(epoch from at) * 1000000)::int8::text from parties
  `;
}

export async function touches(changesets: Array<string>, records: SQL) {
  const result = await db.execute<{ kind: string; id: string; at: string }>(
    touched(
      sql`select id, published_at from changeset where id = any(${sql.param(changesets)}::uuid[])`,
      records,
    ),
  );
  const marks = new Map<string, number>();
  for (const row of result.rows) {
    marks.set(`${row.kind}:${row.id}`, Number(row.at));
  }
  return marks;
}

export function isTouched(
  marks: Map<string, number>,
  id: string,
  parties: Array<string>,
  built: number,
) {
  if ((marks.get(`polity:${id}`) ?? 0) > built) {
    return true;
  }
  for (const party of parties) {
    if ((marks.get(`party:${party}`) ?? 0) > built) {
      return true;
    }
  }
  return false;
}

const SPAN = 86_400_000_000;
const state: {
  synced: number;
  loading: Promise<void> | null;
  seen: Set<string>;
  marks: Map<string, number>;
} = { synced: 0, loading: null, seen: new Set(), marks: new Map() };

async function load() {
  const started = Date.now();
  try {
    const found = await db.execute<{ id: string }>(sql`
      select id::text as id from changeset
      where status = 'published' and published_at > greatest(
        (select min(built_at) from polity_record where built_at > now() - interval '1 day'),
        now() - interval '1 day'
      )
    `);
    const ids = found.rows.map((row) => row.id);
    const unseen = ids.filter((id) => !state.seen.has(id));
    if (unseen.length > 0) {
      const marks = await touches(
        unseen,
        sql`select polity_id, record from polity_record`,
      );
      for (const [key, at] of marks) {
        state.marks.set(key, Math.max(state.marks.get(key) ?? 0, at));
      }
    }
    state.seen = new Set(ids);
    state.synced = started;
  } finally {
    state.loading = null;
  }
}

export async function isCurrent(
  id: string,
  parties: Array<string>,
  built: number,
) {
  const asked = Date.now();
  if (built < asked * 1000 - SPAN) {
    return false;
  }
  try {
    while (state.synced < asked) {
      state.loading ??= load();
      await state.loading;
    }
  } catch {
    return false;
  }
  return !isTouched(state.marks, id, parties, built);
}
