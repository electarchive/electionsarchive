import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { isTouched, touches } from "#lib/server/polity-touched.js";
import { record } from "#lib/server/rpc/archive.js";

export async function rebuild(targets: Array<string> = []) {
  const started = Date.now();
  const latest = await db.execute<{ upto: string }>(sql`
    select coalesce(max(published_at), now())::text as upto from changeset where status = 'published'
  `);
  const upto = latest.rows[0]?.upto ?? new Date().toISOString();
  const listed = await db.execute<{ id: string }>(sql`
    with n as (
      select polity_id as id, count(*) as contests from (
        select b.polity_id from body b join contest x on x.body_id = b.id
        union all
        select o.polity_id from office o join contest x on x.office_id = o.id
      ) t group by polity_id
    )
    select n.id::text as id from n join entity e on e.id = n.id
    where n.contests >= 500 and e.retired_by is null and not exists (select from entity_merge m where m.entity_id = n.id)
    order by n.contests
  `);
  const eligible = listed.rows.map((row) => row.id);
  const isSweep = targets.length === 0;
  let queue = eligible;
  if (isSweep) {
    const stored = await db.execute<{
      id: string;
      built: string;
      parties: Array<string>;
    }>(sql`
      select polity_id::text as id, (extract(epoch from built_at) * 1000000)::int8::text as built,
        array(select p ->> 'id' from jsonb_array_elements(record -> 'parties') p) as parties
      from polity_record
    `);
    const marks = await touches(
      sql`
        select id, published_at from changeset where status = 'published'
          and published_at > (select coalesce(min(built_at), 'infinity') from polity_record)
      `,
      sql`select polity_id, record from polity_record`,
    );
    const rows = new Map(stored.rows.map((row) => [row.id, row]));
    queue = eligible.filter((id) => {
      const row = rows.get(id);
      return (
        row == null || isTouched(marks, id, row.parties, Number(row.built))
      );
    });
  } else if (targets[0] !== "--all") {
    queue = targets;
  }
  const failed: Array<string> = [];
  let built = 0;
  const pending = [...queue];
  await Promise.all(
    Array.from({ length: 4 }, async () => {
      for (let id = pending.pop(); id != null; id = pending.pop()) {
        try {
          const found = await record({ id });
          await db.execute(sql`
            insert into polity_record (polity_id, record, built_at)
            values (${id}::uuid, ${JSON.stringify(found)}::jsonb, ${upto}::timestamptz)
            on conflict (polity_id) do update set record = excluded.record, built_at = excluded.built_at
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
      where built_at < ${upto}::timestamptz and polity_id <> all(${sql.param(failed)}::uuid[])
      returning polity_id
    `);
    kept = bumped.rows.length;
  }
  await db.execute(sql`analyze polity_record`);
  return `${built} polity records rebuilt, ${kept} current through ${upto}, ${failed.length} failed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
