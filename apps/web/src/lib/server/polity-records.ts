import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { record } from "#lib/server/rpc/archive.js";

export async function rebuild() {
  const started = Date.now();
  const listed = await db.execute<{ id: string }>(sql`
    with n as (
      select polity_id as id, count(*) as contests from (
        select b.polity_id from body b join contest x on x.body_id = b.id
        union all
        select o.polity_id from office o join contest x on x.office_id = o.id
      ) t group by polity_id
    )
    select n.id from n join entity e on e.id = n.id
    where n.contests >= 500 and e.retired_by is null and not exists (select from entity_merge m where m.entity_id = n.id)
    order by n.contests
  `);
  const queue = listed.rows.map((row) => row.id);
  let built = 0;
  let failed = 0;
  await Promise.all(
    Array.from({ length: 4 }, async () => {
      for (let id = queue.pop(); id != null; id = queue.pop()) {
        try {
          const found = await record({ id });
          await db.execute(sql`
            insert into polity_record (polity_id, record, built_at)
            values (${id}::uuid, ${JSON.stringify(found)}::jsonb, now())
            on conflict (polity_id) do update set record = excluded.record, built_at = excluded.built_at
          `);
          built += 1;
        } catch (error) {
          failed += 1;
          console.error(id, error);
        }
      }
    }),
  );
  await db.execute(sql`
    delete from polity_record where built_at < ${new Date(started).toISOString()}::timestamptz
  `);
  await db.execute(sql`analyze polity_record`);
  return `${built} polity records, ${failed} failed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
