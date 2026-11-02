import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { db } from "#lib/server/db/index.js";
import { filtersFrom } from "#lib/server/feeds.js";
import { standing, usable } from "#lib/server/standing.js";

export const STATUSES = ["open", "fulfilled", "all"] as const;

export const CATEGORIES = ["known_gap", "blocked"] as const;

export const CLAIM_DAYS = 7;

const PAGE = 50;

export type Bounty = {
  id: string;
  number: number;
  polity: string;
  state: string;
  level: string;
  stage: string;
  years: string;
  grain: string;
  label: string;
  category: (typeof CATEGORIES)[number];
  reason: string;
  sources: string;
  checked: string | null;
  status: "open" | "fulfilled" | "closed";
  claimed_by: string | null;
  claimer: string | null;
  claimer_handle: string | null;
  claimed_until: string | null;
  proposal: number | null;
  proposal_title: string | null;
  filler: string | null;
  filler_handle: string | null;
  closed_at: string | null;
  place: string | null;
  place_path: string | null;
};

const columns = sql`
  b.id::text, b.number, b.polity, b.state, b.level, b.stage, b.years, b.grain, b.label, b.category,
  b.reason, b.sources, b.checked::text, b.status::text as status,
  case when b.claimed_until > now() then b.claimed_by::text end as claimed_by,
  case when b.claimed_until > now() then coalesce(cf.display_name, cf.handle) end as claimer,
  case when b.claimed_until > now() then cf.handle end as claimer_handle,
  case when b.claimed_until > now() then to_char(b.claimed_until at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') end
    as claimed_until,
  p.number as proposal, p.title as proposal_title,
  coalesce(pf.display_name, pf.handle) as filler, pf.handle as filler_handle,
  to_char(b.closed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as closed_at,
  pl.place, pl.place_path
`;

const joins = sql`
  left join profile cf on cf.user_id = b.claimed_by
  left join proposal p on p.id = b.fulfilled_by_proposal
  left join profile pf on pf.user_id = p.author_user_id
  left join lateral (
    select (select n.text from name n where n.entity_id = x.id and n.is_preferred limit 1) as place,
           (select g.path from slug g where g.entity_id = x.id and g.is_current limit 1) as place_path
    from polity x
    where x.iso_code = upper(b.polity) || case when b.state = '' then '' else '-' || b.state end
    limit 1
  ) pl on true
`;

export const BountyFilters = v.object({
  polity: v.optional(v.pipe(v.string(), v.regex(/^[a-z]{2,3}$/u))),
  grain: v.optional(v.pipe(v.string(), v.regex(/^[a-z_]{1,20}$/u))),
  category: v.optional(v.picklist(CATEGORIES)),
  year: v.optional(v.pipe(v.string(), v.regex(/^\d{4}$/u))),
  status: v.optional(v.picklist(STATUSES)),
  after: v.optional(v.pipe(v.string(), v.regex(/^[1-9]\d{0,8}$/u))),
});

export function normalize(found: v.InferOutput<typeof BountyFilters>) {
  return {
    polity: found.polity ?? null,
    grain: found.grain ?? null,
    category: found.category ?? null,
    year: found.year == null ? null : Number(found.year),
    status: found.status ?? "open",
    after: found.after == null ? null : Number(found.after),
  };
}

export const filters = (search: URLSearchParams) =>
  normalize(filtersFrom(BountyFilters, search));

type Filters = ReturnType<typeof normalize>;

const matches = (filters: Omit<Filters, "after">) => sql`
  (${filters.status}::text = 'all' and b.status <> 'closed' or b.status::text = ${filters.status})
  and (${filters.polity}::text is null or b.polity = ${filters.polity})
  and (${filters.grain}::text is null or b.grain = ${filters.grain})
  and (${filters.category}::text is null or b.category = ${filters.category})
  and (${filters.year}::int is null or (
    coalesce(nullif(split_part(b.years, '-', 1), '')::int, 0) <= ${filters.year}::int
    and ${filters.year}::int <= case
      when b.years = '' or b.years like '%-' then 9999
      when b.years like '%-%' then split_part(b.years, '-', 2)::int
      else b.years::int end
  ))
`;

export async function list(filters: Filters) {
  const [{ rows }, { rows: facets }] = await Promise.all([
    db.execute<Bounty>(sql`
      select ${columns}
      from bounty b ${joins}
      where ${matches(filters)} and (${filters.after}::int is null or b.number > ${filters.after}::int)
      order by b.number
      limit ${PAGE + 1}
    `),
    db.execute<{
      kind: "polity" | "grain";
      value: string;
      name: string | null;
      count: number;
    }>(sql`
      select f.kind, f.value, f.count,
             case when f.kind = 'polity' then (
               select n.text from polity x join name n on n.entity_id = x.id and n.is_preferred
               where x.iso_code = upper(f.value) limit 1) end as name
      from (
        select 'polity' as kind, b.polity as value, count(*)::int as count
        from bounty b where ${matches({ ...filters, polity: null })}
        group by b.polity
        union all
        select 'grain', b.grain, count(*)::int
        from bounty b where ${matches({ ...filters, grain: null })}
        group by b.grain
      ) f
      order by f.count desc, f.value
    `),
  ]);
  const page = rows.slice(0, PAGE);
  return {
    bounties: page,
    next: rows.length > PAGE ? (page.at(-1)?.number ?? null) : null,
    polities: facets.filter((row) => row.kind === "polity"),
    grains: facets.filter((row) => row.kind === "grain"),
  };
}

export async function get(number: number) {
  const { rows } = await db.execute<Bounty>(sql`
    select ${columns} from bounty b ${joins} where b.number = ${number}
  `);
  return rows[0] ?? null;
}

export async function open(number: number) {
  const { rows } = await db.execute<{ number: number; label: string }>(sql`
    select number, label from bounty where number = ${number} and status = 'open'
  `);
  return rows[0] ?? null;
}

export async function claim(userId: string, number: number) {
  const found = await standing(userId);
  usable(found);
  if (found.handle == null) {
    error(
      403,
      "Choose a username at /settings/profile before claiming a bounty, so the credit has a name.",
    );
  }
  const { rows } = await db.execute<{ id: string; claimed_until: string }>(sql`
    with held as (
      select id, claimed_by is distinct from ${userId}::uuid or claimed_until <= now() as fresh
      from bounty
      where number = ${number} and status = 'open'
        and (claimed_by is null or claimed_until <= now() or claimed_by = ${userId}::uuid)
    ), claimed as (
      update bounty b set claimed_by = ${userId}::uuid,
                          claimed_until = now() + make_interval(days => ${CLAIM_DAYS})
      from held h
      where b.id = h.id and b.status = 'open'
        and (b.claimed_by is null or b.claimed_until <= now() or b.claimed_by = ${userId}::uuid)
      returning b.id, b.number, b.label, b.polity, b.state, b.claimed_until, h.fresh
    ), logged as (
      insert into activity (actor_user_id, kind, bounty_id, polity_ids, summary)
      select ${userId}::uuid, 'bounty_claimed', c.id,
             array(select p.id from polity p
                   where p.iso_code = upper(c.polity) || case when c.state = '' then '' else '-' || c.state end
                   limit 1),
             format('Bounty %s claimed: %s', c.number, c.label)
      from claimed c where c.fresh
    ), watched as (
      insert into watch (user_id, target_kind, target_id)
      select ${userId}::uuid, 'bounty', c.id from claimed c
      on conflict do nothing
    )
    select id::text, to_char(claimed_until at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as claimed_until
    from claimed
  `);
  const claimed = rows[0];
  if (claimed == null) {
    error(
      409,
      "Someone else holds this bounty, or it isn't open. Watch it to hear when it frees up.",
    );
  }
  return claimed;
}

export async function release(userId: string, number: number) {
  await db.execute(sql`
    update bounty set claimed_by = null, claimed_until = null
    where number = ${number} and claimed_by = ${userId}::uuid
  `);
}

export async function credits(userId: string) {
  const { rows } = await db.execute<{
    number: number;
    label: string;
    proposal: number;
    closed_at: string;
  }>(sql`
    select b.number, b.label, p.number as proposal, b.closed_at::text
    from bounty b join proposal p on p.id = b.fulfilled_by_proposal
    where b.status = 'fulfilled' and p.author_user_id = ${userId}::uuid
    order by b.closed_at desc
    limit 100
  `);
  return rows;
}
