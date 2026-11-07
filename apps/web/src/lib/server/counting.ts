import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";

const FRESH_MS = 120_000;

export type Counting = {
  elections: Map<string, { path: string; date: string; polity: string }>;
  paths: Set<string>;
};

let held: { at: number; value: Promise<Counting> } | null = null;

async function load(): Promise<Counting> {
  const found = await db.execute<{
    id: string;
    path: string;
    date: string;
    polity: string;
    contests: Array<string> | null;
  }>(sql`
    with recursive lit as materialized (
      select c.id, c.election_id from contest c join election e on e.id = c.election_id
      where e.date_from between current_date - 90 and current_date
        and exists (select from live.switch w where w.name = 'results' and w.shown)
        and exists (select from live.frame f where f.contest_id = c.id and f.status = 'active')
        and not exists (
          select from result_set rs where rs.contest_id = c.id and rs.is_default and rs.kind = 'counted'
            and rs.stage in ('official', 'certified', 'court_revised', 'recount')
        )
    ),
    up(id) as (
      select distinct election_id from lit
      union
      select e.parent_id from election e join up on up.id = e.id where e.parent_id is not null
    )
    select e.id, s.path, e.date_from::text as date, e.polity_id as polity,
      (select array_agg(cs.path) from lit join slug cs on cs.entity_id = lit.id and cs.is_current where lit.election_id = e.id) as contests
    from up join election e on e.id = up.id join slug s on s.entity_id = e.id and s.is_current
  `);
  return {
    elections: new Map(
      found.rows.map((row) => [
        row.id,
        { path: row.path, date: row.date, polity: row.polity },
      ]),
    ),
    paths: new Set(
      found.rows.flatMap((row) => [row.path, ...(row.contests ?? [])]),
    ),
  };
}

export async function counting() {
  if (held == null || Date.now() - held.at > FRESH_MS) {
    const value = load();
    held = { at: Date.now(), value };
    value.catch(() => {
      if (held?.value === value) {
        held = null;
      }
    });
  }
  return held.value;
}

const LIVE =
  /^us\/(?:\d{4}-\d{2}-\d{2}-general|[a-z]{2}\/\d{4}-\d{2}-\d{2}-general(?:\/[^/]+)?)$/u;

export function liveOf(now: Counting, path: string | null) {
  return path != null && LIVE.test(path) && now.paths.has(path)
    ? `live/${path}`
    : null;
}

export async function liveFor(path: string) {
  return liveOf(await counting(), path);
}
