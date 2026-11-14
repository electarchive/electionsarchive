import type { SQL } from "drizzle-orm";
import { sql } from "drizzle-orm";
import type { Cover } from "#lib/server/rpc/archive.js";
import { db } from "#lib/server/db/index.js";
import { cover, KEPT, served, topped } from "#lib/server/rpc/archive.js";

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

const own = (ids: Array<string>) => sql`
  select r.id as root, a.k, a.id as at, null::int as alt
  from contest r
  cross join lateral (
    select a.k, a.id from area a where a.id = coalesce(r.district_area_id, (
      select pt.area_id from election e
      join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
      where e.id = r.election_id limit 1
    ))
  ) a
  where r.id = any(${sql.param(ids)}::uuid[])
`;

const counts = (root: SQL) => sql`
  coalesce((
    select jsonb_agg(jsonb_build_object('code', u.code, 'units', u.units) order by u.code)
    from (
      select ru.scheme_code as code, count(*)::int as units
      from (
        select distinct v.unit_k from result_set rs
        cross join lateral (select unit_k from result_value where result_set_k = rs.k offset 0) v
        where rs.contest_id = ${root} and rs.status = 'active' and ${served("rs")}
      ) x
      join reporting_unit ru on ru.k = x.unit_k and ru.area_relation = 'within' and ru.kind = 'standard'
      group by ru.scheme_code
    ) u
  ), '[]')
`;

const rebuilt = (ids: Array<string>) => {
  const input = own(ids);
  return sql`
    select i.root, i.k, ${counts(sql`i.root`)} as units,
      coalesce(jsonb_agg(jsonb_build_object(
        'place', t.place, 'person', t.person, 'party', t.party, 'kind', t.kind, 'label', t.label,
        'measure', t.measure, 'votes', t.votes, 'total', t.total, 'won', t.won
      ) order by t.place) filter (where t.root is not null), '[]') as tops,
      coalesce(array_agg(distinct t.person) filter (where t.inferred), '{}') as inferred,
      array_remove(array_agg(distinct t.person) || array_agg(distinct t.party), null) as refs
    from (${input}) i
    left join (${topped(input, true)}) t on t.root = i.root
    group by i.root, i.k
  `;
};

async function locked(table: string, write: SQL, size: number) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      return await db.transaction(async (tx) => {
        await tx.execute(sql`set local lock_timeout = '500ms'`);
        await tx.execute(
          sql`lock table ${sql.identifier(table)} in share row exclusive mode`,
        );
        const { rows } = await tx.execute<Record<string, unknown>>(write);
        return rows.length;
      });
    } catch (error) {
      if (!busy(error)) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 15_000));
    }
  }
  console.error(`gave up on ${size} contests while applies held locks`);
  return 0;
}

const fill = async (ids: Array<string>) =>
  locked(
    "contest_cover",
    sql`
      insert into contest_cover
        (id, series, election, indirect, date, year, status, is_remainder, notional, district, members, decided, reach)
      select c.id, c.series, c.election, c.indirect, c.date, c.year, c.status, c."isRemainder", c.notional,
        c.district, c.members, c.decided, c.reach
      from (${live(ids)}) c
      on conflict (id) do nothing
      returning id
    `,
    ids.length,
  );

const fillTops = async (ids: Array<string>) =>
  locked(
    "contest_top",
    sql`
      insert into contest_top (contest_id, area_k, tops, inferred, refs, units)
      select b.root, b.k, b.tops, b.inferred, b.refs, b.units from (${rebuilt(ids)}) b
      on conflict (contest_id) do nothing
      returning contest_id
    `,
    ids.length,
  );

const fillUnits = async (ids: Array<string>) =>
  locked(
    "contest_top",
    sql`
      update contest_top t set units = ${counts(sql`t.contest_id`)}
      where t.contest_id = any(${sql.param(ids)}::uuid[]) and t.units is null
      returning t.contest_id
    `,
    ids.length,
  );

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

type Built = {
  root: string;
  k: number;
  tops: unknown;
  inferred: Array<string>;
  units: unknown;
  built_at?: Date;
};

async function auditTops() {
  const stored = await db.execute<Built>(sql`
    select t.contest_id as root, t.area_k as k, t.tops, t.inferred, t.units, t.built_at
    from contest_top t tablesample bernoulli (1) limit ${SAMPLE}
  `);
  const fresh = new Map<string, Built>();
  for (let start = 0; start < stored.rows.length; start += BATCH) {
    const found = await db.execute<Built>(
      rebuilt(stored.rows.slice(start, start + BATCH).map((row) => row.root)),
    );
    for (const row of found.rows) {
      fresh.set(row.root, row);
    }
  }
  const stale: Array<string> = [];
  for (const row of stored.rows) {
    const now = fresh.get(row.root);
    const kept = JSON.stringify([row.k, row.tops, row.inferred, row.units]);
    const current =
      now == null
        ? null
        : JSON.stringify([
            now.k,
            now.tops,
            now.inferred,
            row.units == null ? null : now.units,
          ]);
    if (kept !== current) {
      stale.push(row.root);
      console.error(
        `stale contest top ${row.root}: built ${row.built_at?.toISOString()}, stored ${kept}, live ${current}`,
      );
    }
  }
  if (stale.length > 0) {
    await db.execute(
      sql`delete from contest_top where contest_id = any(${sql.param(stale)}::uuid[])`,
    );
  }
  return { checked: stored.rows.length, stale: stale.length };
}

async function refillTops() {
  const started = Date.now();
  const orphans = await db.execute<{ id: string }>(sql`
    delete from contest_top t
    where not exists (select from contest c where c.id = t.contest_id and c.allocation_parent_id is null)
    returning t.contest_id as id
  `);
  const listed = await db.execute<{ id: string }>(sql`
    select c.id from contest c
    where c.allocation_parent_id is null and not exists (select from contest_top t where t.contest_id = c.id)
      and (c.district_area_id is not null or exists (
        select from election e
        join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
        where e.id = c.election_id
      ))
    order by count(*) over (partition by c.district_area_id, c.body_id, c.office_id) > 1 desc,
      c.district_area_id nulls last, c.id
  `);
  const ids = listed.rows.map((row) => row.id);
  let built = 0;
  for (let start = 0; start < ids.length; start += BATCH) {
    built += await fillTops(ids.slice(start, start + BATCH));
    if (start % (BATCH * 100) === 0) {
      console.warn(
        `${Math.min(start + BATCH, ids.length)} of ${ids.length} race tops`,
      );
    }
  }
  const bare = await db.execute<{ id: string }>(sql`
    select contest_id as id from contest_top where units is null order by contest_id
  `);
  let counted = 0;
  for (let start = 0; start < bare.rows.length; start += BATCH) {
    counted += await fillUnits(
      bare.rows.slice(start, start + BATCH).map((row) => row.id),
    );
    if (start % (BATCH * 100) === 0) {
      console.warn(
        `${Math.min(start + BATCH, bare.rows.length)} of ${bare.rows.length} unit counts`,
      );
    }
  }
  await db.execute(sql`analyze contest_top`);
  const { checked, stale } = await auditTops();
  return `${built} race tops built, ${counted} unit counts filled, ${orphans.rows.length} orphans removed, ${stale} of ${checked} sampled rows stale and removed, in ${Math.round((Date.now() - started) / 1000)}s`;
}

async function refillCovers() {
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

export async function refill() {
  return `${await refillCovers()}; ${await refillTops()}`;
}
