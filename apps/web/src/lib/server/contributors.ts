import type { SQL } from "drizzle-orm";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { ProposalState } from "#lib/contrib.js";
import type { Entry } from "#lib/server/atom.js";
import { db } from "#lib/server/db/index.js";

export const HANDLE = /^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$/u;

export const Handle = v.pipe(
  v.string(),
  v.trim(),
  v.toLowerCase(),
  v.regex(HANDLE, "A handle is 3 to 30 lowercase letters, digits, or hyphens."),
);

export type ContributorLink = { url: string; label: string };

export type ContributorRow = {
  handle: string;
  display_name: string | null;
  affiliation: string | null;
  member_since: string;
  accepted: number;
  proposals: number;
};

export async function contributors({
  q,
  after,
}: {
  q: string | null;
  after: string | null;
}) {
  const { rows } = await db.execute<ContributorRow>(sql`
    select p.handle, p.display_name, p.affiliation, (u.created_at at time zone 'UTC')::date::text as member_since,
           count(r.id) filter (where r.state in ('approved', 'published'))::int as accepted,
           count(r.id)::int as proposals
    from profile p
    join users u on u.id = p.user_id
    left join proposal r on r.author_user_id = p.user_id and r.submitted_at is not null
    where (${q}::text is null
           or starts_with(p.handle, lower(${q}))
           or strpos(lower(p.display_name), lower(${q})) > 0)
      and (${after}::text is null or p.handle > ${after})
    group by p.user_id, p.handle, u.created_at
    order by p.handle
    limit 101
  `);
  return {
    contributors: rows.slice(0, 100),
    next: rows.length > 100 ? (rows[99]?.handle ?? null) : null,
  };
}

export async function contributor(handle: string) {
  const { rows } = await db.execute<{
    user_id: string;
    handle: string;
    display_name: string | null;
    affiliation: string | null;
    bio_md: string | null;
    links: Array<ContributorLink>;
    member_since: string;
  }>(sql`
    select p.user_id, p.handle, p.display_name, p.affiliation, p.bio_md, p.links,
           (u.created_at at time zone 'UTC')::date::text as member_since
    from profile p join users u on u.id = p.user_id
    where p.handle = ${handle}
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "No contributor has that handle.");
  }
  const [proposals, agents] = await Promise.all([
    db.execute<{
      number: number;
      title: string;
      state: ProposalState;
      updated_at: string;
      agent_name: string | null;
    }>(sql`
      select number, title, state::text as state, updated_at::text, agent_name
      from proposal
      where author_user_id = ${found.user_id} and submitted_at is not null
      order by number desc
      limit 500
    `),
    db.execute<{ name: string; model: string | null; proposals: number }>(sql`
      select agent_name as name, max(agent_model) as model, count(*)::int as proposals
      from proposal
      where author_user_id = ${found.user_id} and submitted_at is not null and agent_name is not null
      group by agent_name
      order by count(*) desc, agent_name
    `),
  ]);
  const counts = { accepted: 0, open: 0, rejected: 0, reverted: 0 };
  for (const item of proposals.rows) {
    if (item.state === "approved" || item.state === "published") {
      counts.accepted += 1;
    } else if (item.state === "open" || item.state === "changes_requested") {
      counts.open += 1;
    } else if (item.state === "rejected" || item.state === "reverted") {
      counts[item.state] += 1;
    }
  }
  const { user_id: userId, ...profile } = found;
  return {
    userId,
    profile,
    counts,
    proposals: proposals.rows,
    agents: agents.rows,
  };
}

export async function activityEntries(where: SQL, limit = 50) {
  const { rows } = await db.execute<Entry>(sql`
    select a.id, a.summary as title, a.at::text as updated,
           coalesce(f.display_name, f.handle) as author,
           case
             when p.number is not null
               then '/proposals/' || p.number || coalesce('#comment-' || a.comment_id, '')
             when t.kind = 'talk'
               then '/talk/' || ea_public_id(e.type, e.id) || '/' || t.id || coalesce('#comment-' || a.comment_id, '')
           end as path
    from activity a
    left join proposal p on p.id = a.proposal_id
    left join thread t on t.id = a.thread_id
    left join entity e on e.id = t.entity_id
    left join profile f on f.user_id = a.actor_user_id
    where (a.proposal_id is null or p.submitted_at is not null) and (${where})
    order by a.at desc
    limit ${limit}
  `);
  return rows;
}
