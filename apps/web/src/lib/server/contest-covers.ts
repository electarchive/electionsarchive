import { sql } from "drizzle-orm";
import type { Cover } from "#lib/server/rpc/archive.js";
import { db } from "#lib/server/db/index.js";
import { cover, KEPT } from "#lib/server/rpc/archive.js";

const BATCH = 500;
const SAMPLE = 5000;

const busy = (error: unknown) => {
  for (let at: unknown = error; at instanceof Error; at = at.cause) {
    if ("code" in at && at.code === "55P03") {
      return true;
    }
  }
  return false;
};

const live = (ids: Array<string>) =>
  cover(sql`select unnest(${sql.param(ids)}::uuid[]) as id`);

async function fill(ids: Array<string>) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      return await db.transaction(async (tx) => {
        await tx.execute(sql`set local lock_timeout = '500ms'`);
        await tx.execute(
          sql`lock table contest_cover in share row exclusive mode`,
        );
        const { rows } = await tx.execute<{ id: string }>(sql`
          insert into contest_cover
            (id, series, election, indirect, date, year, status, is_remainder, notional, district, members, decided, reach)
          select c.id, c.series, c.election, c.indirect, c.date, c.year, c.status, c."isRemainder", c.notional,
            c.district, c.members, c.decided, c.reach
          from (${live(ids)}) c
          on conflict (id) do nothing
          returning id
        `);
        return rows.length;
      });
    } catch (error) {
      if (!busy(error)) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 15_000));
    }
  }
  console.error(`gave up on ${ids.length} contests while applies held locks`);
  return 0;
}

async function audit() {
  const stored = await db.execute<Cover>(sql`
    select ${KEPT} from contest_cover k tablesample bernoulli (1) limit ${SAMPLE}
  `);
  const fresh = new Map<string, Cover>();
  for (let start = 0; start < stored.rows.length; start += BATCH) {
    const found = await db.execute<Cover>(
      live(stored.rows.slice(start, start + BATCH).map((row) => row.id)),
    );
    for (const row of found.rows) {
      fresh.set(row.id, row);
    }
  }
  const stale: Array<string> = [];
  for (const row of stored.rows) {
    const now = fresh.get(row.id);
    if (now == null || JSON.stringify(row) !== JSON.stringify({ ...now })) {
      stale.push(row.id);
      console.error(
        `stale contest cover ${row.id}: stored ${JSON.stringify(row)}, live ${JSON.stringify(now ?? null)}`,
      );
    }
  }
  if (stale.length > 0) {
    await db.execute(
      sql`delete from contest_cover where id = any(${sql.param(stale)}::uuid[])`,
    );
  }
  return { checked: stored.rows.length, stale: stale.length };
}

export async function refill() {
  const started = Date.now();
  const orphans = await db.execute<{ id: string }>(sql`
    delete from contest_cover k
    where not exists (select from contest c where c.id = k.id and c.allocation_parent_id is null)
    returning k.id
  `);
  const listed = await db.execute<{ id: string }>(sql`
    select c.id from contest c
    where c.allocation_parent_id is null and not exists (select from contest_cover k where k.id = c.id)
    order by c.district_area_id nulls last, c.id
  `);
  const ids = listed.rows.map((row) => row.id);
  let built = 0;
  for (let start = 0; start < ids.length; start += BATCH) {
    built += await fill(ids.slice(start, start + BATCH));
    if (start % (BATCH * 100) === 0) {
      console.warn(
        `${Math.min(start + BATCH, ids.length)} of ${ids.length} contests`,
      );
    }
  }
  await db.execute(sql`analyze contest_cover`);
  const { checked, stale } = await audit();
  return `${built} contest covers built, ${orphans.rows.length} orphans removed, ${stale} of ${checked} sampled rows stale and removed, in ${Math.round((Date.now() - started) / 1000)}s`;
}
