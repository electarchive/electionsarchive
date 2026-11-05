import type { SQL } from "drizzle-orm";
import { READ_ONLY } from "$app/env/public";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { randomBytes } from "node:crypto";
import * as v from "valibot";
import type { FeedEntry, ProposalState } from "#lib/contrib.js";
import type { Entry } from "#lib/server/atom.js";
import { byline, describe, PROPOSAL_STATES } from "#lib/contrib.js";
import { m } from "#lib/paraglide/messages.js";
import { changeCount, shown } from "#lib/server/contrib.js";
import { HANDLE } from "#lib/server/contributors.js";
import { db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";
import { talkEntity } from "#lib/server/talk.js";

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

export async function polityAt(path: string) {
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

const Tier = v.picklist(["1", "2", "3"]);

export const ProposalFilters = v.object({
  state: v.optional(v.picklist(PROPOSAL_STATES)),
  tier: v.optional(Tier),
  polity: v.optional(v.pipe(v.string(), v.regex(POLITY_PATH))),
  author: v.optional(v.pipe(v.string(), v.toLowerCase(), v.regex(HANDLE))),
  entity: v.optional(v.pipe(v.string(), v.minLength(1), v.maxLength(40))),
  before: v.optional(v.pipe(v.string(), v.regex(/^[1-9]\d{0,8}$/u))),
});

export type ProposalQuery = v.InferOutput<typeof ProposalFilters>;

export const filtersFrom = <
  T extends v.ObjectSchema<v.ObjectEntries, undefined>,
>(
  schema: T,
  search: URLSearchParams,
) => {
  const raw = Object.fromEntries(
    Object.keys(schema.entries).flatMap((key) => {
      const value = search.get(key)?.trim();
      return value == null || value === "" ? [] : [[key, value]];
    }),
  );
  const parsed = v.safeParse(schema, raw);
  if (!parsed.success) {
    error(400, parsed.issues[0].message);
  }
  return parsed.output;
};

export async function proposalList(query: ProposalQuery) {
  const [polity, entity] = await Promise.all([
    query.polity == null ? null : polityAt(query.polity),
    query.entity == null ? null : talkEntity(query.entity),
  ]);
  const { rows } = await db.execute<{
    number: number;
    title: string;
    state: ProposalState;
    tier: number | null;
    author: string | null;
    handle: string | null;
    agent_name: string | null;
    polity_id: string | null;
    submitted_at: string;
    decided_at: string | null;
    changes: number;
  }>(sql`
    select p.number, p.title, p.state::text as state, p.tier,
           coalesce(f.display_name, f.handle, a.name) as author, f.handle, p.agent_name, p.polity_id,
           p.submitted_at::text, p.decided_at::text, ${changeCount(sql`r`)} as changes
    from proposal p
    left join users a on a.id = p.author_user_id
    left join profile f on f.user_id = p.author_user_id
    left join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
    where p.submitted_at is not null and ${shown(sql`p`)}
      and (${query.state ?? null}::text is null or p.state::text = ${query.state ?? null}::text)
      and (${query.tier ?? null}::int is null or p.tier = ${query.tier ?? null}::int)
      and (${query.author ?? null}::text is null or f.handle = ${query.author ?? null}::text)
      and (${entity?.id ?? null}::text is null or r.diff -> 'entities' ? ${entity?.id ?? null}::text)
      and (${polity?.id ?? null}::uuid is null
           or p.polity_id = any(${tree(sql`select ${polity?.id ?? null}::uuid`)}))
      and (${query.before ?? null}::int is null or p.number < ${query.before ?? null}::int)
    order by p.number desc
    limit ${PAGE + 1}
  `);
  const page = rows.slice(0, PAGE);
  const { full } = await names([
    ...new Set(
      page.flatMap((row) => (row.polity_id == null ? [] : [row.polity_id])),
    ),
  ]);
  return {
    polity,
    entity,
    proposals: page.map(({ polity_id: polityId, ...row }) => ({
      ...row,
      polity: polityId == null ? null : (full.get(polityId) ?? null),
    })),
    next: rows.length > PAGE ? String(page.at(-1)?.number) : null,
  };
}

export const AUTHOR_KINDS = ["person", "agent", "pipeline"] as const;

export const RecentFilters = v.object({
  polity: v.optional(v.pipe(v.string(), v.regex(POLITY_PATH))),
  author: v.optional(v.picklist(AUTHOR_KINDS)),
  tier: v.optional(Tier),
  before: v.optional(v.pipe(v.string(), v.isoTimestamp())),
});

export type RecentQuery = v.InferOutput<typeof RecentFilters>;

export const visible = sql`
  (a.proposal_id is null or (p.submitted_at is not null and ${shown(sql`p`)}))
  and (a.comment_id is null or c.hidden_at is null)
  and a.kind <> 'changeset_published'
`;

export async function stream({
  activity,
  imports,
  before,
  after = null,
  limit = PAGE,
}: {
  activity: SQL | null;
  imports: SQL | null;
  before: string | null;
  after?: string | null;
  limit?: number;
}) {
  const parts = [
    activity == null
      ? null
      : sql`
        select a.id::text, a.at, a.kind::text as kind,
               coalesce(f.display_name, f.handle) as author, f.handle,
               case when a.agent_key_id is not null then coalesce(c.agent_name, p.agent_name) end as agent,
               p.number, coalesce(b.label, p.title, t.title) as title, p.tier, p.state::text as state,
               b.number as bounty,
               t.id::text as thread, t.entity_id::text as entity, a.comment_id::text as comment,
               a.polity_ids[1]::text as polity, null::text as pipeline, 1 as batches
        from activity a
        left join proposal p on p.id = a.proposal_id
        left join thread t on t.id = a.thread_id
        left join comment c on c.id = a.comment_id
        left join profile f on f.user_id = a.actor_user_id
        left join bounty b on b.id = a.bounty_id
        where ${visible}
          and (${before}::timestamptz is null or a.at < ${before}::timestamptz)
          and (${after}::timestamptz is null or a.at >= ${after}::timestamptz)
          and (${activity})
      `,
    imports == null
      ? null
      : sql`
        select concat_ws('/', 'imported', c.pipeline, coalesce(k.iso, 'none'), (c.published_at at time zone 'UTC')::date,
                         left(md5(c.summary), 12)) as id,
               max(c.published_at) as at,
               'imported' as kind, null as author, null as handle, null as agent, null::int as number,
               c.summary as title, null::int as tier, null as state, null::int as bounty, null as thread, null as entity, null as comment,
               (select p.id::text from polity p where p.iso_code = k.iso limit 1) as polity,
               c.pipeline, count(*)::int as batches
        from changeset c
        cross join lateral (select c.scope ->> 'polity' as iso) k
        where c.status = 'published' and c.author_kind in ('pipeline', 'system')
          and (${before}::timestamptz is null or c.published_at < ${before}::timestamptz)
          and (${after}::timestamptz is null or c.published_at >= ${after}::timestamptz)
          and (${imports})
        group by c.pipeline, c.summary, k.iso, (c.published_at at time zone 'UTC')::date
      `,
  ].filter((part) => part != null);
  if (parts.length === 0) {
    return { entries: [], next: null };
  }
  const { rows } = await db.execute<
    Omit<FeedEntry, "at" | "entity" | "polity" | "polity_path"> & {
      at: string;
      entity: string | null;
      polity: string | null;
    }
  >(sql`
    select id, to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') as at, kind, author,
           handle, agent, number, title, tier, state, bounty, thread, entity, comment, polity, pipeline, batches
    from (${sql.join(parts, sql` union all `)}) s
    order by s.at desc
    limit ${limit + 1}
  `);
  const page = rows.slice(0, limit);
  const ids = [
    ...new Set(
      page.flatMap((row) =>
        [row.entity, row.polity].filter((id) => id != null),
      ),
    ),
  ];
  const [{ full }, paths] = await Promise.all([
    names(ids),
    ids.length === 0
      ? { rows: [] }
      : db.execute<{ id: string; path: string | null; public_id: string }>(sql`
        select e.id, ea_public_id(e.type, e.id) as public_id,
               (select s.path from slug s where s.entity_id = e.id and s.is_current limit 1) as path
        from entity e where e.id = any(${sql.param(ids)}::uuid[])
      `),
  ]);
  const at = new Map(paths.rows.map((row) => [row.id, row]));
  return {
    entries: page.map((row): FeedEntry => ({
      ...row,
      entity:
        row.entity == null
          ? null
          : {
              name: full.get(row.entity) ?? at.get(row.entity)?.public_id ?? "",
              public_id: at.get(row.entity)?.public_id ?? row.entity,
            },
      polity: row.polity == null ? null : (full.get(row.polity) ?? null),
      polity_path:
        row.polity == null ? null : (at.get(row.polity)?.path ?? null),
    })),
    next: rows.length > limit ? (page.at(-1)?.at ?? null) : null,
  };
}

export async function recent(query: RecentQuery) {
  const polity = query.polity == null ? null : await polityAt(query.polity);
  const within = polity == null ? null : tree(sql`select ${polity.id}::uuid`);
  const activity =
    READ_ONLY || query.author === "pipeline"
      ? null
      : sql`
        (${within == null} or a.polity_ids && ${within ?? sql`'{}'::uuid[]`})
        and case ${query.author ?? null}::text
              when 'agent' then a.agent_key_id is not null
              when 'person' then a.actor_user_id is not null and a.agent_key_id is null
              else true
            end
        and (${query.tier ?? null}::int is null or p.tier = ${query.tier ?? null}::int)
      `;
  const importing =
    (query.author == null || query.author === "pipeline") && query.tier == null;
  const imports = importing
    ? sql`
      (${within == null} or exists (
              select from polity x where x.iso_code = k.iso
              and x.id = any(${within ?? sql`'{}'::uuid[]`})
            ))
    `
    : null;
  return {
    polity,
    ...(await stream({ activity, imports, before: query.before ?? null })),
  };
}

export const atomEntries = (entries: Array<FeedEntry>): Array<Entry> =>
  entries.map((entry) => {
    const { text, href } = describe(entry);
    return {
      id: entry.id,
      title:
        entry.batches > 1
          ? `${text} (${m.feed_batches({ count: entry.batches })})`
          : text,
      updated: entry.at,
      author: byline(entry),
      path: href,
    };
  });

export async function contributorStream(userId: string) {
  const { entries } = await stream({
    activity: sql`
      a.actor_user_id = ${userId}::uuid
            or (p.author_user_id = ${userId}::uuid
                and a.kind in ('changes_requested', 'proposal_rejected', 'proposal_published', 'proposal_reverted'))
    `,
    imports: null,
    before: null,
  });
  return entries;
}

export const WATCH_KINDS = [
  "entity",
  "polity",
  "proposal",
  "user",
  "bounty",
] as const;

export type WatchKind = (typeof WATCH_KINDS)[number];

const watched = (userId: string, kind: WatchKind) => sql`
  select target_id from watch where user_id = ${userId}::uuid and target_kind = ${kind}::watch_target
`;

export const watchedStream = (userId: string) => {
  const polities = tree(sql`
    select target_id from watch
    where user_id = ${userId}::uuid and target_kind = 'polity' and descendants
  `);
  return {
    activity: sql`
      a.proposal_id in (${watched(userId, "proposal")})
      or a.bounty_id in (${watched(userId, "bounty")})
      or a.actor_user_id in (${watched(userId, "user")})
      or a.entity_ids && array(${watched(userId, "entity")})
      or t.entity_id in (${watched(userId, "entity")})
      or a.polity_ids && array(${watched(userId, "polity")})
      or a.polity_ids && ${polities}
    `,
    imports: sql`
      exists (
              select from polity x where x.iso_code = k.iso
              and (x.id in (${watched(userId, "polity")}) or x.id = any(${polities}))
            )
    `,
  };
};

export async function watchlist(userId: string, before: string | null) {
  const [stream_, mine, targets] = await Promise.all([
    stream({ ...watchedStream(userId), before }),
    before == null
      ? stream({
          activity: sql`
            p.author_user_id = ${userId}::uuid and a.actor_user_id is distinct from ${userId}::uuid
                        and a.at > now() - interval '14 days'
          `,
          imports: null,
          before: null,
          limit: 10,
        })
      : { entries: [] },
    watches(userId),
  ]);
  return { ...stream_, mine: mine.entries, targets };
}

export async function watches(userId: string) {
  const { rows } = await db.execute<{
    kind: WatchKind;
    id: string;
    descendants: boolean;
    created_at: string;
    number: number | null;
    title: string | null;
    handle: string | null;
    person: string | null;
    public_id: string | null;
    path: string | null;
  }>(sql`
    select w.target_kind::text as kind, w.target_id::text as id, w.descendants, w.created_at::text,
           coalesce(p.number, b.number) as number, coalesce(p.title, b.label) as title, f.handle, coalesce(f.display_name, f.handle) as person,
           case when e.id is not null then ea_public_id(e.type, e.id) end as public_id,
           (select s.path from slug s where s.entity_id = e.id and s.is_current limit 1) as path
    from watch w
    left join proposal p on w.target_kind = 'proposal' and p.id = w.target_id
    left join profile f on w.target_kind = 'user' and f.user_id = w.target_id
    left join entity e on w.target_kind in ('entity', 'polity') and e.id = w.target_id
    left join bounty b on w.target_kind = 'bounty' and b.id = w.target_id
    where w.user_id = ${userId}::uuid
    order by w.created_at desc
  `);
  const { full } = await names(
    rows.flatMap((row) =>
      row.kind === "entity" || row.kind === "polity" ? [row.id] : [],
    ),
  );
  return rows.map((row) => ({
    ...row,
    name:
      row.kind === "proposal" || row.kind === "bounty"
        ? row.title
        : row.kind === "user"
          ? row.person
          : (full.get(row.id) ?? row.public_id),
  }));
}

export async function watching(
  userId: string | undefined,
  kind: WatchKind,
  id: string,
) {
  if (userId == null) {
    return false;
  }
  const { rows } = await db.execute<{ on: boolean }>(sql`
    select exists (select from watch where user_id = ${userId}::uuid
                   and target_kind = ${kind}::watch_target and target_id = ${id}::uuid) as on
  `);
  return rows[0]?.on ?? false;
}

export async function setWatch(
  userId: string,
  kind: WatchKind,
  id: string,
  on: boolean,
) {
  if (!on) {
    await db.execute(sql`
      delete from watch where user_id = ${userId}::uuid
        and target_kind = ${kind}::watch_target and target_id = ${id}::uuid
    `);
    return;
  }
  const { rows } = await db.execute<{ ok: boolean }>(sql`
    select case ${kind}::text
      when 'proposal' then exists (select from proposal where id = ${id}::uuid and submitted_at is not null)
      when 'user' then exists (select from profile where user_id = ${id}::uuid)
      when 'polity' then exists (select from polity where id = ${id}::uuid)
      when 'bounty' then exists (select from bounty where id = ${id}::uuid)
      else exists (select from entity where id = ${id}::uuid and retired_by is null)
    end as ok
  `);
  if (rows[0]?.ok !== true) {
    error(404, "There is nothing with that ID to watch.");
  }
  await db.execute(sql`
    insert into watch (user_id, target_kind, target_id, descendants)
    values (${userId}::uuid, ${kind}::watch_target, ${id}::uuid, ${kind === "polity"})
    on conflict do nothing
  `);
}

export async function feedToken(userId: string, rotate = false) {
  const { rows } = await db.execute<{ token: string | null }>(
    sql`
      update profile set feed_token = case
        when ${rotate} or feed_token is null then ${randomBytes(24).toString("hex")}
        else feed_token end
      where user_id = ${userId}::uuid
      returning feed_token as token
    `,
  );
  return rows[0] ?? null;
}

export async function tokenOwner(token: string) {
  if (!/^[0-9a-f]{48}$/u.test(token)) {
    return null;
  }
  const { rows } = await db.execute<{ user_id: string; handle: string }>(sql`
    select user_id, handle from profile where feed_token = ${token}
  `);
  return rows[0] ?? null;
}
