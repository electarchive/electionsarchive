import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { ProposalState } from "#lib/contrib.js";
import type { Level, LimitKind } from "#lib/server/standing.js";
import { auth } from "#lib/server/auth.js";
import { roles } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import { standing } from "#lib/server/standing.js";

export const LIMIT_KINDS = [
  "cap",
  "probation",
  "no_upload",
  "no_talk",
  "no_artifacts",
] as const satisfies Array<LimitKind>;

export const Reason = v.pipe(
  v.string(),
  v.normalize("NFC"),
  v.trim(),
  v.minLength(1, "Give a reason."),
  v.maxLength(2000),
);

const Days = v.pipe(
  v.string(),
  v.trim(),
  v.transform((value) => (value === "" ? null : Number(value))),
  v.nullable(v.pipe(v.number(), v.integer(), v.minValue(1), v.maxValue(3650))),
);

export const LimitForm = v.object({
  kind: v.picklist(LIMIT_KINDS),
  proposals: v.pipe(
    v.string(),
    v.trim(),
    v.transform((value) => (value === "" ? null : Number(value))),
    v.nullable(
      v.pipe(v.number(), v.integer(), v.minValue(0), v.maxValue(10_000)),
    ),
  ),
  reason: Reason,
  days: Days,
});

export const BanForm = v.object({ reason: Reason, days: Days });

export const RevertForm = v.object({
  since: v.pipe(v.string(), v.isoDate("Pick a date.")),
  reason: Reason,
});

export async function moderator(userId: string | undefined) {
  const allowed = await roles(userId);
  if (userId == null || !(allowed.admin || allowed.reviewer)) {
    error(403, "Only reviewers can manage accounts.");
  }
  return { userId, admin: allowed.admin };
}

export type AccountRow = {
  id: string;
  name: string;
  handle: string | null;
  email: string;
  level: Level;
  accepted: number;
  rejected: number;
  reverted: number;
  open: number;
  limited: boolean;
  banned: boolean;
  active_at: string | null;
};

export async function accounts(q: string | null) {
  const { rows } = await db.execute<AccountRow>(sql`
    with activity_at as (
      select author_user_id as user_id, max(coalesce(submitted_at, created_at)) as at
      from proposal group by author_user_id
      union all
      select author_user_id, max(created_at) from comment where author_user_id is not null group by author_user_id
    )
    select u.id, u.name, f.handle, u.email,
           contributor_level(u.id)::int as level,
           count(p.id) filter (where p.state in ('approved', 'published'))::int as accepted,
           count(p.id) filter (where p.state = 'rejected')::int as rejected,
           count(p.id) filter (where p.state = 'reverted')::int as reverted,
           count(p.id) filter (where p.state in ('open', 'changes_requested'))::int as open,
           exists (select from account_limit l where l.user_id = u.id and l.lifted_at is null
                   and (l.expires_at is null or l.expires_at > now())) as limited,
           coalesce(u.banned, false) and (u.ban_expires is null or u.ban_expires > now()) as banned,
           (select max(a.at) from activity_at a where a.user_id = u.id)::text as active_at
    from users u
    left join profile f on f.user_id = u.id
    left join proposal p on p.author_user_id = u.id and p.submitted_at is not null
    where ${q}::text is null
       or starts_with(f.handle, lower(${q}))
       or strpos(lower(u.name), lower(${q})) > 0
       or lower(u.email) = lower(${q})
    group by u.id, f.handle
    order by (select max(a.at) from activity_at a where a.user_id = u.id) desc nulls last, u.created_at desc
    limit 200
  `);
  return rows;
}

export async function account(id: string) {
  const { rows } = await db.execute<{
    id: string;
    name: string;
    email: string;
    handle: string | null;
    created_at: string;
    ban_reason: string | null;
    ban_expires: string | null;
  }>(sql`
    select u.id, u.name, u.email, f.handle, u.created_at::text, u.ban_reason, u.ban_expires::text
    from users u left join profile f on f.user_id = u.id
    where u.id = ${id}
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "No account has that ID.");
  }
  const [state, limits, published, reverts] = await Promise.all([
    standing(id),
    db.execute<{
      id: string;
      kind: LimitKind;
      value: { proposals?: number } | null;
      reason: string;
      set_by: string;
      created_at: string;
      expires_at: string | null;
      lifted_by: string | null;
      lifted_at: string | null;
      active: boolean;
    }>(sql`
      select l.id, l.kind::text as kind, l.value, l.reason, s.name as set_by, l.created_at::text,
             l.expires_at::text, x.name as lifted_by, l.lifted_at::text,
             l.lifted_at is null and (l.expires_at is null or l.expires_at > now()) as active
      from account_limit l
      join users s on s.id = l.set_by
      left join users x on x.id = l.lifted_by
      where l.user_id = ${id}
      order by l.created_at desc
      limit 100
    `),
    db.execute<{
      number: number;
      title: string;
      state: ProposalState;
      tier: number | null;
      decided_at: string | null;
    }>(sql`
      select number, title, state::text as state, tier, decided_at::text
      from proposal
      where author_user_id = ${id} and submitted_at is not null
      order by coalesce(decided_at, submitted_at) desc
      limit 200
    `),
    db.execute<{
      number: number;
      title: string;
      status: string;
      error: string | null;
      created_at: string;
    }>(sql`
      select p.number, p.title, j.status::text as status, j.output ->> 'error' as error, j.created_at::text
      from proposal_job j
      join proposal p on p.id = j.proposal_id
      where p.author_user_id = ${id} and j.kind = 'revert'
      order by j.created_at desc
      limit 100
    `),
  ]);
  return {
    account: found,
    standing: state,
    limits: limits.rows,
    proposals: published.rows,
    reverts: reverts.rows,
  };
}

async function guard(actor: { userId: string; admin: boolean }, id: string) {
  if (id === actor.userId) {
    error(400, "You can't limit or ban your own account.");
  }
  const target = await roles(id);
  if ((target.admin || target.reviewer) && !actor.admin) {
    error(403, "Only an admin can limit or ban a reviewer.");
  }
}

export async function setLimit(
  actor: { userId: string; admin: boolean },
  id: string,
  form: v.InferOutput<typeof LimitForm>,
) {
  await guard(actor, id);
  if (form.kind === "cap" && form.proposals == null) {
    error(400, "Set the number of proposals a day for a cap.");
  }
  await db.execute(sql`
    insert into account_limit (user_id, kind, value, reason, set_by, expires_at)
    values (${id}, ${form.kind}::account_limit_kind,
            ${form.kind === "cap" ? JSON.stringify({ proposals: form.proposals }) : null}::jsonb,
            ${form.reason}, ${actor.userId},
            case when ${form.days}::int is null then null else now() + make_interval(days => ${form.days}::int) end)
  `);
}

export async function liftLimit(
  actor: { userId: string; admin: boolean },
  id: string,
  limitId: string,
) {
  await guard(actor, id);
  await db.execute(sql`
    update account_limit set lifted_by = ${actor.userId}, lifted_at = now()
    where id = ${limitId} and user_id = ${id} and lifted_at is null
  `);
}

export async function ban(
  actor: { userId: string; admin: boolean },
  id: string,
  form: v.InferOutput<typeof BanForm>,
) {
  await guard(actor, id);
  const context = await auth.$context;
  await context.internalAdapter.updateUser(id, {
    banned: true,
    banReason: form.reason,
    banExpires:
      form.days == null ? null : new Date(Date.now() + form.days * 86_400_000),
    updatedAt: new Date(),
  });
  await context.internalAdapter.deleteUserSessions(id);
}

export async function unban(
  actor: { userId: string; admin: boolean },
  id: string,
) {
  await guard(actor, id);
  const context = await auth.$context;
  await context.internalAdapter.updateUser(id, {
    banned: false,
    banReason: null,
    banExpires: null,
    updatedAt: new Date(),
  });
}

export async function revertSince(
  actor: { userId: string; admin: boolean },
  id: string,
  form: v.InferOutput<typeof RevertForm>,
) {
  if (!actor.admin) {
    error(403, "Only an admin can revert an account's work.");
  }
  const { rows } = await db.transaction(async (tx) => {
    const queued = await tx.execute<{ number: number }>(sql`
      with targets as (
        select id, number, decided_at from proposal
        where author_user_id = ${id} and state = 'published' and decided_at >= ${form.since}::date
          and not exists (select from proposal_job j where j.proposal_id = proposal.id
                          and j.kind = 'revert' and j.status in ('queued', 'running'))
      ),
      jobs as (
        insert into proposal_job (proposal_id, kind, requested_by, output, created_at)
        select id, 'revert', ${actor.userId}, jsonb_build_object('reason', ${form.reason}::text),
               now() + row_number() over (order by decided_at desc) * interval '1 millisecond'
        from targets
        returning proposal_id
      )
      select t.number from jobs join targets t on t.id = jobs.proposal_id
    `);
    await tx.execute(
      sql`select pg_notify('proposal_job', json_build_object('kind', 'queued')::text)`,
    );
    return queued;
  });
  return rows.length;
}
