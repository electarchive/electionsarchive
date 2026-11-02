import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { count } from "#lib/format.js";
import { db } from "#lib/server/db/index.js";

export type Level = 0 | 1 | 2 | 3 | 4;

export type LimitKind =
  | "cap"
  | "probation"
  | "no_upload"
  | "no_talk"
  | "no_artifacts";

export type ActiveLimit = {
  kind: LimitKind;
  value: { proposals?: number } | null;
  reason: string;
  expires_at: string | null;
};

const MB = 1024 ** 2;

const size = (value: number) =>
  value >= 1024 * MB
    ? `${count(value / (1024 * MB))} GB`
    : `${count(value / MB)} MB`;

export const LEVELS: Record<
  Level,
  {
    proposals: number | null;
    uploads: number;
    ops: number | null;
    artifacts: boolean;
  }
> = {
  0: { proposals: 5, uploads: 50 * MB, ops: 50, artifacts: false },
  1: { proposals: 20, uploads: 500 * MB, ops: 500, artifacts: false },
  2: { proposals: 100, uploads: 2048 * MB, ops: null, artifacts: true },
  3: { proposals: 500, uploads: 10_240 * MB, ops: null, artifacts: true },
  4: { proposals: null, uploads: 10_240 * MB, ops: null, artifacts: true },
};

export async function standing(userId: string) {
  const { rows } = await db.execute<{
    verified: boolean;
    banned: boolean;
    handle: string | null;
    licensed: boolean;
    level: Level;
    submitted: number;
    uploaded: string;
    limits: Array<ActiveLimit>;
  }>(sql`
    select u.email_verified as verified,
           coalesce(u.banned, false) and (u.ban_expires is null or u.ban_expires > now()) as banned,
           p.handle, p.license_accepted_at is not null as licensed,
           contributor_level(u.id)::int as level,
           (select count(*)::int from proposal q
            where q.author_user_id = u.id and q.submitted_at > now() - interval '1 day') as submitted,
           (select coalesce(sum(x.bytes), 0)::text from upload x
            where x.owner_user_id = u.id and x.created_at > now() - interval '1 day') as uploaded,
           coalesce((
             select jsonb_agg(jsonb_build_object('kind', l.kind, 'value', l.value, 'reason', l.reason,
                                                 'expires_at', l.expires_at) order by l.created_at desc)
             from account_limit l
             where l.user_id = u.id and l.lifted_at is null and (l.expires_at is null or l.expires_at > now())
           ), '[]') as limits
    from users u
    left join profile p on p.user_id = u.id
    where u.id = ${userId}
  `);
  const found = rows[0];
  if (found == null) {
    error(403, "This account no longer exists.");
  }
  const caps = LEVELS[found.level];
  const override = found.limits.find((limit) => limit.kind === "cap")?.value
    ?.proposals;
  const has = (kind: LimitKind) =>
    found.limits.some((limit) => limit.kind === kind);
  return {
    ...found,
    uploaded: Number(found.uploaded),
    proposals: override ?? caps.proposals,
    uploads: has("no_upload") ? 0 : caps.uploads,
    ops: caps.ops,
    artifacts: caps.artifacts && !has("no_artifacts"),
    probation: has("probation"),
    talk: !has("no_talk"),
  };
}

export type Standing = Awaited<ReturnType<typeof standing>>;

export function usable(found: Standing) {
  if (found.banned) {
    error(403, "This account is banned.");
  }
  if (!found.verified) {
    error(403, "Verify your email address first.");
  }
}

export async function submittable(
  userId: string,
  ops: number,
  { counted = true }: { counted?: boolean } = {},
) {
  const found = await standing(userId);
  usable(found);
  if (found.handle == null || !found.licensed) {
    error(
      403,
      "Choose a username and license your contributions under CC BY 4.0 at /settings/profile before proposing changes.",
    );
  }
  if (found.ops != null && ops > found.ops) {
    error(
      413,
      `At your level a proposal can hold up to ${count(found.ops)} changes, and this one has ${count(ops)}. Split it into smaller proposals.`,
    );
  }
  if (
    counted &&
    found.proposals != null &&
    found.submitted >= found.proposals
  ) {
    error(
      429,
      `You can submit ${count(found.proposals)} ${found.proposals === 1 ? "proposal" : "proposals"} a day. Your agents' proposals count too. Try again tomorrow.`,
    );
  }
  return found;
}

export async function uploadable(userId: string, length: number | null) {
  const found = await standing(userId);
  usable(found);
  if (found.uploads === 0) {
    error(403, "This account can't upload files.");
  }
  if (found.uploaded + (length ?? 0) > found.uploads) {
    error(
      429,
      `This file would take you past your ${size(found.uploads)} daily upload limit. Try again tomorrow.`,
    );
  }
  return found;
}

export async function talkable(userId: string) {
  const found = await standing(userId);
  usable(found);
  if (!found.talk) {
    error(403, "This account can't comment right now.");
  }
  return found;
}
