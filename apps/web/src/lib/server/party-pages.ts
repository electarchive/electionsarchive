import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { contents } from "#lib/server/pages.js";

export async function rebuild() {
  const started = Date.now();
  const listed = await db.execute<{ id: string }>(sql`
    with used as (
      select distinct party_id as id from contest_option where party_id is not null
    ),
    owners as (
      select coalesce(m.into_id, u.id) as id from used u left join entity_merge m on m.entity_id = u.id
      union
      select pa.parent_party_id from used u
      join party_affiliation pa on pa.party_id = u.id and pa.kind = 'regional_branch'
    )
    select o.id from owners o join entity e on e.id = o.id
    where e.retired_by is null and not exists (select from entity_merge m where m.entity_id = o.id)
    order by o.id
  `);
  const queue = listed.rows.map((row) => row.id);
  let built = 0;
  let failed = 0;
  await Promise.all(
    Array.from({ length: 4 }, async () => {
      for (let id = queue.pop(); id != null; id = queue.pop()) {
        try {
          const page = await contents(id);
          await db.execute(sql`
            insert into party_page (party_id, page, built_at)
            values (${id}::uuid, ${JSON.stringify(page)}::jsonb, now())
            on conflict (party_id) do update set page = excluded.page, built_at = excluded.built_at
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
    delete from party_page where built_at < ${new Date(started).toISOString()}::timestamptz
  `);
  await db.execute(sql`analyze party_page`);
  return `${built} party pages, ${failed} failed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
