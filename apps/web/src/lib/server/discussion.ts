import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Anchor, CommentView } from "#lib/contrib.js";
import { PROJECT } from "#lib/constants.js";
import { commentLimit } from "#lib/contrib.js";
import { mentionsOf } from "#lib/markdown.js";
import { roles } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";

export type Author = {
  userId: string;
  keyId: string | null;
  agentName: string | null;
  agentModel: string | null;
};

const Label = v.pipe(v.string(), v.trim(), v.minLength(1), v.maxLength(200));
const Revision = v.pipe(v.number(), v.integer(), v.minValue(1));

export const AnchorSchema = v.variant("kind", [
  v.object({
    kind: v.literal("row"),
    revision: Revision,
    table: v.pipe(v.string(), v.regex(/^[a-z_]{1,63}$/u)),
    key: v.record(
      v.pipe(v.string(), v.regex(/^[a-z_]{1,63}$/u)),
      v.pipe(v.string(), v.maxLength(200)),
    ),
    label: Label,
  }),
  v.object({
    kind: v.literal("evidence"),
    revision: Revision,
    key: v.pipe(v.string(), v.regex(/^[\w-]{1,40}$/u)),
    label: Label,
  }),
]);

export const Body = v.pipe(
  v.string(),
  v.normalize("NFC"),
  v.trim(),
  v.minLength(1, "Write a comment first."),
  v.maxLength(commentLimit, "A comment is limited to 10,000 characters."),
);

const limit = createRateLimiter({ prefix: `${PROJECT}:comments` });

async function allowed(userId: string) {
  const { rows } = await db.execute<{ verified: boolean; banned: boolean }>(sql`
    select email_verified as verified,
           coalesce(banned, false) and (ban_expires is null or ban_expires > now()) as banned
    from users where id = ${userId}
  `);
  const found = rows[0];
  if (found == null || found.banned) {
    error(403, "This account can't comment.");
  }
  if (!found.verified) {
    error(403, "Verify your email address before commenting.");
  }
  const { isAllowed, retryAfter } = await limit(
    {
      name: "comments",
      rules: [
        { limit: 10, window: 60 },
        { limit: 200, window: 86_400 },
      ],
    },
    userId,
  );
  if (!isAllowed) {
    error(
      429,
      `You can post 10 comments a minute and 200 a day. Try again in ${retryAfter} s.`,
    );
  }
}

export async function threadComments(
  threadId: string | null,
  viewer: string | null,
): Promise<Array<CommentView>> {
  if (threadId == null) {
    return [];
  }
  const allowedRoles = await roles(viewer ?? undefined);
  const isModerator = allowedRoles.admin || allowedRoles.reviewer;
  const { rows } = await db.execute<CommentView>(sql`
    select c.id, c.parent_id, u.name as author, p.handle, c.agent_name, c.agent_model, c.revision_n,
           case when c.hidden_at is null or ${isModerator} or c.author_user_id = ${viewer}::uuid
                then c.body_md end as body,
           c.anchor, c.created_at::text, c.edited_at::text, c.hidden_reason, h.name as hidden_by,
           c.author_user_id is not distinct from ${viewer}::uuid and ${viewer}::uuid is not null as mine
    from comment c
    left join users u on u.id = c.author_user_id
    left join profile p on p.user_id = c.author_user_id
    left join users h on h.id = c.hidden_by
    where c.thread_id = ${threadId}
    order by c.created_at, c.id
  `);
  return rows;
}

export async function proposalThread(proposalId: string) {
  const { rows } = await db.execute<{ id: string }>(sql`
    select id from thread where proposal_id = ${proposalId}
  `);
  return rows[0]?.id ?? null;
}

export async function commentOnProposal({
  author,
  number,
  body,
  parentId,
  anchor,
}: {
  author: Author;
  number: number;
  body: string;
  parentId: string | null;
  anchor: Anchor | null;
}) {
  await allowed(author.userId);
  const { rows } = await db.execute<{
    id: string;
    author_user_id: string | null;
    submitted: boolean;
    head: number | null;
  }>(sql`
    select p.id, p.author_user_id, p.submitted_at is not null as submitted,
           (select max(n) from proposal_revision r where r.proposal_id = p.id) as head
    from proposal p where p.number = ${number}
  `);
  const found = rows[0];
  if (
    found == null ||
    (!found.submitted && found.author_user_id !== author.userId)
  ) {
    error(404, "No proposal has that number.");
  }
  if (anchor != null) {
    const { rows: anchored } = await db.execute<{ ok: boolean }>(sql`
      select exists (
        select from proposal_revision r
        where r.proposal_id = ${found.id} and r.n = ${anchor.revision}
          and case ${anchor.kind}
                when 'evidence' then coalesce(r.payload -> 'evidence', '{}') ? ${anchor.kind === "evidence" ? anchor.key : ""}
                else exists (
                  select from jsonb_array_elements(r.diff -> 'rows') x
                  where x ->> 'table' = ${anchor.kind === "row" ? anchor.table : ""}
                    and x -> 'key' = ${JSON.stringify(anchor.kind === "row" ? anchor.key : {})}::jsonb
                )
              end
      ) as ok
    `);
    if (anchored[0]?.ok !== true) {
      error(400, "That change or evidence item isn't in this proposal.");
    }
  }
  return db.transaction(async (tx) => {
    await tx.execute(sql`
      insert into thread (kind, proposal_id, author_user_id)
      values ('proposal', ${found.id}, ${found.author_user_id})
      on conflict (proposal_id) do nothing
    `);
    const { rows: inserted } = await tx.execute<{ id: string }>(sql`
      with t as (select id from thread where proposal_id = ${found.id}),
           parent as (
             select coalesce(c.parent_id, c.id) as id, c.anchor from comment c, t
             where c.id = ${parentId}::uuid and c.thread_id = t.id
           )
      insert into comment (thread_id, parent_id, author_user_id, agent_key_id, agent_name, agent_model,
                           revision_n, body_md, anchor)
      select t.id, (select id from parent), ${author.userId}, ${author.keyId}, ${author.agentName},
             ${author.agentModel}, ${found.head},
             ${body}, coalesce(${anchor == null ? null : JSON.stringify(anchor)}::jsonb, (select anchor from parent))
      from t
      where ${parentId}::uuid is null or exists (select from parent)
      returning id
    `);
    const comment = inserted[0];
    if (comment == null) {
      error(400, "The comment you're replying to isn't on this proposal.");
    }
    await tx.execute(sql`
      insert into activity (actor_user_id, agent_key_id, kind, proposal_id, changeset_id, thread_id, comment_id,
                            entity_ids, polity_ids, notify_user_ids, summary)
      select ${author.userId}, ${author.keyId}, 'commented', p.id, p.head_changeset_id, c.thread_id, c.id,
             coalesce((select array_agg(e::uuid) from proposal_revision r, jsonb_array_elements_text(r.diff -> 'entities') e
                       where r.proposal_id = p.id and r.changeset_id = p.head_changeset_id), '{}'),
             array_remove(array[p.polity_id], null),
             array(
               select distinct x from unnest(
                 array_remove(array[p.author_user_id], null)
                 || array(select f.user_id from profile f where f.handle = any(${mentionsOf(body)}::text[]))
               ) x
               where x <> ${author.userId}::uuid
             ),
             format('Comment on proposal %s', p.number)
      from proposal p, comment c
      where p.id = ${found.id} and c.id = ${comment.id}
    `);
    await tx.execute(sql`
      insert into watch (user_id, target_kind, target_id)
      values (${author.userId}, 'proposal', ${found.id})
      on conflict do nothing
    `);
    return comment.id;
  });
}

export async function editComment({
  userId,
  id,
  body,
}: {
  userId: string;
  id: string;
  body: string;
}) {
  await allowed(userId);
  const { rows } = await db.execute<{ id: string }>(sql`
    update comment set body_md = ${body}, edited_at = now()
    where id = ${id} and author_user_id = ${userId} and hidden_at is null
    returning id
  `);
  if (rows.length === 0) {
    error(404, "You can only edit your own comments that aren't hidden.");
  }
}

export async function hideComment({
  userId,
  id,
  reason,
}: {
  userId: string;
  id: string;
  reason: string | null;
}) {
  const allowedRoles = await roles(userId);
  if (!(allowedRoles.admin || allowedRoles.reviewer)) {
    error(403, "Only reviewers can hide comments.");
  }
  if (reason === "") {
    error(400, "Hiding a comment needs a reason.");
  }
  const { rows } = await db.execute<{ id: string }>(sql`
    update comment
    set hidden_by = ${reason == null ? null : userId}::uuid,
        hidden_at = case when ${reason}::text is null then null else now() end,
        hidden_reason = ${reason}
    where id = ${id}
    returning id
  `);
  if (rows.length === 0) {
    error(404);
  }
}
