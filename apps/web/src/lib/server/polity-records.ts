import { sql } from "drizzle-orm";
import { follow, keeps, traced } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { isTouched, touches } from "#lib/server/polity-touched.js";
import { record } from "#lib/server/rpc/archive.js";

export async function rebuild(targets: Array<string> = []) {
  const started = Date.now();
  follow();
  const latest = await db.execute<{ upto: string }>(sql`
    select coalesce(max(published_at), now())::text as upto from changeset where status = 'published'
  `);
  const upto = latest.rows[0]?.upto ?? new Date().toISOString();
  const listed = await db.execute<{ id: string; contests: number }>(sql`
    with n as (
      select polity_id as id, count(*) as contests from (
        select b.polity_id from body b join contest x on x.body_id = b.id
        union all
        select o.polity_id from office o join contest x on x.office_id = o.id
      ) t group by polity_id
    )
    select n.id::text as id, n.contests::int as contests from n join entity e on e.id = n.id
    where n.contests >= 500 and e.retired_by is null and not exists (select from entity_merge m where m.entity_id = n.id)
    order by n.contests
  `);
  const eligible = listed.rows.map((row) => row.id);
  const isSweep = targets.length === 0;
  let queue = eligible;
  const held: Array<string> = [];
  if (isSweep) {
    const stored = await db.execute<{
      id: string;
      built: string;
      parties: Array<string>;
      mark: string | null;
      deps: Array<string> | null;
    }>(sql`
      select polity_id::text as id, (extract(epoch from built_at) * 1000000)::int8::text as built,
        array(select p ->> 'id' from jsonb_array_elements(record -> 'parties') p) as parties,
        mark::text as mark, deps
      from polity_record
    `);
    const floor = await db.execute<{
      at: string | null;
      built: string | null;
    }>(sql`
      select at::text as at, (extract(epoch from at) * 1000000)::int8::text as built
      from (select mode() within group (order by built_at) as at from polity_record) f
    `);
    const since = floor.rows[0];
    const published = await db.execute<{ id: string }>(sql`
      select id::text as id from changeset
      where status = 'published' and published_at > ${since?.at ?? "infinity"}::timestamptz
    `);
    const marks = await touches(
      published.rows.map((row) => row.id),
      sql`select polity_id, record from polity_record`,
    );
    const oldest = Number(since?.built ?? 0);
    const rows = new Map(stored.rows.map((row) => [row.id, row]));
    queue = [];
    for (const id of eligible) {
      const row = rows.get(id);
      if (row == null) {
        queue.push(id);
        continue;
      }
      const built = Number(row.built);
      const isStale =
        built < oldest || isTouched(marks, id, row.parties, built);
      const byKeys = await keeps(
        row.mark == null ? null : Number(row.mark),
        row.deps,
      );
      if (byKeys === false || (byKeys == null && isStale)) {
        queue.push(id);
      } else if (isStale) {
        held.push(id);
      }
    }
  } else if (targets[0] !== "--all") {
    queue = targets;
  }
  const failed: Array<string> = [];
  let built = 0;
  const contests = new Map(listed.rows.map((row) => [row.id, row.contests]));
  const heavy = queue.filter((id) => (contests.get(id) ?? 0) >= 40_000);
  const light = queue.filter((id) => (contests.get(id) ?? 0) < 40_000);
  await Promise.all(
    Array.from({ length: 4 }, async (_, worker) => {
      const next = () =>
        worker === 0 ? (heavy.pop() ?? light.pop()) : light.pop();
      for (let id = next(); id != null; id = next()) {
        try {
          const found = await traced(async () => record({ id }));
          const rows = await db.execute<{ id: string }>(sql`
            select id::text as id from entity
            where id = any(array(
              select k::uuid from unnest(${sql.param(found.deps ?? [])}::text[]) k
              where k ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            )) and type in ('party', 'person')
          `);
          const named = new Set(rows.rows.map((row) => row.id));
          const deps =
            found.deps?.map((key) => (named.has(key) ? `row:${key}` : key)) ??
            null;
          await db.execute(sql`
            insert into polity_record (polity_id, record, built_at, mark, deps)
            values (${id}::uuid, ${JSON.stringify(found.value)}::jsonb, ${upto}::timestamptz, ${found.mark}, ${deps == null ? null : sql.param(deps)}::text[])
            on conflict (polity_id) do update set record = excluded.record, built_at = excluded.built_at, mark = excluded.mark, deps = excluded.deps
          `);
          built += 1;
        } catch (error) {
          failed.push(id);
          console.error(id, error);
        }
      }
    }),
  );
  let kept = 0;
  if (isSweep || targets[0] === "--all") {
    await db.execute(sql`
      delete from polity_record where polity_id <> all(${sql.param(eligible)}::uuid[])
    `);
    const bumped = await db.execute<{ polity_id: string }>(sql`
      update polity_record set built_at = ${upto}::timestamptz
      where built_at < ${upto}::timestamptz and polity_id <> all(${sql.param([...failed, ...held])}::uuid[])
      returning polity_id
    `);
    kept = bumped.rows.length;
  }
  await db.execute(sql`analyze polity_record`);
  return `${built} polity records rebuilt, ${kept} current through ${upto}, ${held.length} kept by key versions, ${failed.length} failed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
