import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { crownChunk } from "#lib/server/rpc/archive.js";

const CHUNK = 300;

const parents = sql`
  select k.parent_id as id from election k
  join slug s on s.entity_id = k.id and s.is_current
  where k.parent_id is not null
  group by k.parent_id having count(*) >= 40
`;

async function scope(target: string) {
  if (target === "--all") {
    return sql`select id from (${parents}) p`;
  }
  const found = await db.execute<{ id: string; type: string }>(sql`
    select e.id, e.type::text as type from slug s join entity e on e.id = s.entity_id
    where s.path = ${target.replaceAll(/^\/+|\/+$/g, "")} and s.is_current limit 1
  `);
  const entity = found.rows[0];
  if (entity == null) {
    throw new Error(`no election or polity at ${target}`);
  }
  return entity.type === "election"
    ? sql`select id from (${parents}) p where p.id = ${entity.id}::uuid`
    : sql`
      select p.id from (${parents}) p join election e on e.id = p.id
      where e.polity_id = ${entity.id}::uuid
    `;
}

async function dirty(since: string | null, upto: string) {
  const window =
    since == null
      ? sql`select null::uuid as id where false`
      : sql`
        select id from changeset where status = 'published'
          and published_at > ${since}::timestamptz and published_at <= ${upto}::timestamptz
      `;
  return sql`
    with cs as materialized (${window}),
    el as materialized (
      select id from election where changeset_id in (select id from cs)
      union select id from election_history where superseded_by in (select id from cs)
    ), ct as materialized (
      select id, election_id from contest where changeset_id in (select id from cs)
      union select id, election_id from contest_history where superseded_by in (select id from cs)
    ), rs as materialized (
      select id, election_id from result_set where changeset_id in (select id from cs)
      union select id, election_id from result_set_history where superseded_by in (select id from cs)
    ), op as materialized (
      select id, contest_id from contest_option where changeset_id in (select id from cs)
      union select id, contest_id from contest_option_history where superseded_by in (select id from cs)
    ), moved as materialized (
      select entity_id as id from entity_merge where changeset_id in (select id from cs)
      union select entity_id from entity_retirement where changeset_id in (select id from cs)
      union select area_id from area_parent where changeset_id in (select id from cs)
      union select area_id from area_parent_history where superseded_by in (select id from cs)
      union select area_id from area_geometry where changeset_id in (select id from cs)
      union select area_id from area_geometry_history where superseded_by in (select id from cs)
    ), hit as (
      select id from el
      union select election_id from ct
      union select election_id from rs
      union select r.election_id from result_set r where r.id in (
        select result_set_id from outcome where changeset_id in (select id from cs)
        union select result_set_id from outcome_history where superseded_by in (select id from cs)
        except select id from rs
      )
      union select x.election_id from contest x where x.id in (
        (
          select contest_id from op
          union select q.contest_id from contest_option q where q.id in (
            select option_id from candidacy where changeset_id in (select id from cs)
            union select option_id from candidacy_history where superseded_by in (select id from cs)
            except select id from op
          )
        )
        except select id from ct
      )
      union select w.election_id from election_crown w
        cross join lateral unnest(w.chain || array[w.party_id, w.person_id, w.area_id, w.group_id, w.shaped_id]) a(id)
        join moved m on m.id = a.id
    )
    select k.parent_id as parent, k.id from election k
    join (${parents}) p on p.id = k.parent_id
    left join hit h on h.id = k.id
    where exists (select from slug s where s.entity_id = k.id and s.is_current)
      and (h.id is not null or not exists (select from election_crown w where w.election_id = k.id))
  `;
}

async function store(parent: string, ids: Array<string>) {
  const rows = await crownChunk(parent, ids);
  await db.transaction(async (tx) => {
    await tx.execute(
      sql`delete from election_crown where election_id = any(${sql.param(ids)}::uuid[])`,
    );
    await tx.execute(sql`
      insert into election_crown
        (election_id, parent_id, office, party_id, person_id, label, area_id, group_id, shaped_id, is_led, votes, chain)
      select r.election, ${parent}::uuid, r.office, r.party, r.person, r.label, r.area, r."group", r.shaped,
        r.led, r.votes, r.chain
      from jsonb_to_recordset(${JSON.stringify(rows)}::jsonb) r(
        election uuid, office text, party uuid, person uuid, label text, area uuid, "group" uuid,
        shaped uuid, led boolean, votes jsonb, chain uuid[]
      )
    `);
  });
  return rows.length;
}

export async function rebuild(target?: string) {
  const started = Date.now();
  const latest = await db.execute<{ upto: string | null }>(sql`
    select max(published_at)::text as upto from changeset where status = 'published'
  `);
  const upto = latest.rows[0]?.upto ?? "-infinity";
  let since: string | null = null;
  if (target == null) {
    const mark = await db.execute<{ since: string | null }>(sql`
      select coalesce(
        (select published_at from election_crown_mark),
        (select min(built_at) from election_crown)
      )::text as since
    `);
    since = mark.rows[0]?.since ?? null;
  }
  const removed = await db.execute<{ id: string }>(sql`
    delete from election_crown w
    where not exists (
      select from election k join (${parents}) p on p.id = k.parent_id
      where k.id = w.election_id and k.parent_id = w.parent_id
    )
    returning w.election_id as id
  `);
  const listed = await db.execute<{ parent: string; id: string }>(
    target == null
      ? await dirty(since, upto)
      : sql`
        select k.parent_id as parent, k.id from election k
        where k.parent_id in (${await scope(target)})
          and exists (select from slug s where s.entity_id = k.id and s.is_current)
      `,
  );
  const queue = Map.groupBy(listed.rows, (row) => row.parent)
    .entries()
    .flatMap(([parent, rows]) =>
      Map.groupBy(rows, (_row, index) => Math.floor(index / CHUNK))
        .values()
        .map((chunk) => ({ parent, ids: chunk.map((row) => row.id) })),
    )
    .toArray()
    .toReversed();
  const total = queue.length;
  let built = 0;
  let failed = 0;
  let done = 0;
  await Promise.all(
    Array.from({ length: 3 }, async () => {
      for (let job = queue.pop(); job != null; job = queue.pop()) {
        try {
          const stored = await store(job.parent, job.ids);
          built += stored;
        } catch (error) {
          failed += job.ids.length;
          console.error(job.parent, error);
        }
        done += 1;
        if (done % 50 === 0) {
          console.warn(
            `${done} of ${total} chunks, ${built} crowns, ${Math.round((Date.now() - started) / 1000)}s`,
          );
        }
      }
    }),
  );
  if (target == null && failed === 0) {
    await db.execute(sql`
      insert into election_crown_mark (id, published_at) values (true, ${upto}::timestamptz)
      on conflict (id) do update set published_at = excluded.published_at
    `);
  }
  await db.execute(sql`analyze election_crown`);
  return `${built} election crowns from ${listed.rows.length} children, ${failed} failed, ${removed.rows.length} removed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
