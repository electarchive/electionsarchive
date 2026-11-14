import type { SQL } from "drizzle-orm";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { FeedEntry } from "#lib/changes.js";
import type { Entry } from "#lib/server/atom.js";
import { importedBy } from "#lib/changes.js";
import { m } from "#lib/paraglide/messages.js";
import { db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";

const PAGE = 50;

const POLITY_PATH = /^[a-z0-9][a-z0-9/-]{0,200}$/u;

const tree = (roots: SQL) => sql`
  array(
    with recursive tree(id) as (
      ${roots}
      union
      select pp.polity_id from polity_parent pp join tree on pp.parent_id = tree.id
    )
    select id from tree
  )
`;

async function polityAt(path: string) {
  if (!POLITY_PATH.test(path)) {
    error(404, "No polity has that path.");
  }
  const { rows } = await db.execute<{ id: string; path: string }>(sql`
    select p.id, s.path from slug s join polity p on p.id = s.entity_id
    where s.path = ${path} and s.is_current
    limit 1
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "No polity has that path.");
  }
  const { full } = await names([found.id]);
  return { ...found, name: full.get(found.id) ?? found.path };
}

export const RecentFilters = v.object({
  polity: v.optional(v.pipe(v.string(), v.regex(POLITY_PATH))),
  before: v.optional(v.pipe(v.string(), v.isoTimestamp())),
});

export function recentFilters(search: URLSearchParams) {
  const raw = Object.fromEntries(
    ["polity", "before"].flatMap((key) => {
      const value = search.get(key)?.trim();
      return value == null || value === "" ? [] : [[key, value]];
    }),
  );
  const parsed = v.safeParse(RecentFilters, raw);
  if (!parsed.success) {
    error(400, parsed.issues[0].message);
  }
  return parsed.output;
}

export async function recent(query: v.InferOutput<typeof RecentFilters>) {
  const polity = query.polity == null ? null : await polityAt(query.polity);
  const within = polity == null ? null : tree(sql`select ${polity.id}::uuid`);
  const before = query.before ?? null;
  const { rows } = await db.execute<
    Omit<FeedEntry, "polity" | "polity_path"> & { polity: string | null }
  >(sql`
    select id, to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') as at, title, polity, pipeline, batches
    from (
      select concat_ws('/', 'imported', c.pipeline, coalesce(k.iso, 'none'), (c.published_at at time zone 'UTC')::date,
                       left(md5(c.summary), 12)) as id,
             max(c.published_at) as at, c.summary as title,
             (select p.id::text from polity p where p.iso_code = k.iso limit 1) as polity,
             c.pipeline, count(*)::int as batches
      from changeset c
      cross join lateral (select c.scope ->> 'polity' as iso) k
      where c.status = 'published' and c.author_kind in ('pipeline', 'system')
        and (${before}::timestamptz is null or c.published_at < ${before}::timestamptz)
        and (${within == null} or exists (
          select from polity x where x.iso_code = k.iso
          and x.id = any(${within ?? sql`'{}'::uuid[]`})
        ))
      group by c.pipeline, c.summary, k.iso, (c.published_at at time zone 'UTC')::date
    ) s
    order by s.at desc
    limit ${PAGE + 1}
  `);
  const page = rows.slice(0, PAGE);
  const ids = [
    ...new Set(page.flatMap((row) => (row.polity == null ? [] : [row.polity]))),
  ];
  const [{ full }, paths] = await Promise.all([
    names(ids),
    ids.length === 0
      ? { rows: [] }
      : db.execute<{ id: string; path: string | null }>(sql`
        select e.id, (select s.path from slug s where s.entity_id = e.id and s.is_current limit 1) as path
        from entity e where e.id = any(${sql.param(ids)}::uuid[])
      `),
  ]);
  const at = new Map(paths.rows.map((row) => [row.id, row.path]));
  return {
    polity,
    entries: page.map((row): FeedEntry => ({
      ...row,
      polity: row.polity == null ? null : (full.get(row.polity) ?? null),
      polity_path: row.polity == null ? null : (at.get(row.polity) ?? null),
    })),
    next: rows.length > PAGE ? (page.at(-1)?.at ?? null) : null,
  };
}

export const atomEntries = (entries: Array<FeedEntry>): Array<Entry> =>
  entries.map((entry) => ({
    id: entry.id,
    title:
      entry.batches > 1
        ? `${entry.title} (${m.feed_batches({ count: entry.batches })})`
        : entry.title,
    updated: entry.at,
    author: importedBy(entry.pipeline),
    path: entry.polity_path == null ? null : `/${entry.polity_path}`,
  }));
