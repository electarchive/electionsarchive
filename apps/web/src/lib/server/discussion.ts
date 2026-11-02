import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Anchor, CommentView, DiffRow } from "#lib/contrib.js";
import { PROJECT } from "#lib/constants.js";
import { anchorId, commentLimit } from "#lib/contrib.js";
import { mentionsOf } from "#lib/markdown.js";
import { roles } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";
import { talkable } from "#lib/server/standing.js";

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

export const SuggestionSchema = v.object({
  values: v.pipe(
    v.record(
      v.pipe(v.string(), v.regex(/^[a-z_]{1,63}$/u)),
      v.pipe(v.string(), v.normalize("NFC"), v.maxLength(commentLimit)),
    ),
    v.check(
      (values) => Object.keys(values).length > 0,
      "A suggestion changes at least one field.",
    ),
  ),
});

const SUGGESTED = ["text", "body_md", "value", "color"];

type Op = { op?: unknown; table?: unknown; id?: unknown; values?: unknown };

const same = (a: unknown, b: unknown) =>
  JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value);

export function suggestable(
  ops: Array<Op>,
  row: DiffRow,
): { op: number; fields: Record<string, string> } | null {
  const matches: Array<{ op: number; values: Record<string, unknown> }> = [];
  for (const [index, op] of ops.entries()) {
    const values = op.values;
    if (op.table !== row.table || !isRecord(values)) {
      continue;
    }
    const isSet =
      op.op === "set" && row.op === "change" && op.id === row.key.id;
    const isAdd =
      op.op === "add" &&
      row.op === "add" &&
      Object.entries(values).every(
        ([field, value]) =>
          row.fields[field] == null || same(row.fields[field][1], value),
      );
    if (isSet || isAdd) {
      matches.push({ op: index, values });
    }
  }
  const match = matches.length === 1 ? matches[0] : undefined;
  if (match == null) {
    return null;
  }
  const fields: Record<string, string> = {};
  for (const field of SUGGESTED) {
    const value = match.values[field];
    if (typeof value === "string") {
      fields[field] = value;
    }
  }
  return Object.keys(fields).length > 0 ? { op: match.op, fields } : null;
}

export function suggestableRows(
  ops: Array<Op>,
  rows: Array<DiffRow>,
  revision: number,
) {
  const found: Record<string, Record<string, string>> = {};
  for (const row of rows) {
    const match = suggestable(ops, row);
    if (match != null) {
      found[
        anchorId({
          kind: "row",
          revision,
          table: row.table,
          key: row.key,
          label: "",
        })
      ] = match.fields;
    }
  }
  return found;
}

const limit = createRateLimiter({ prefix: `${PROJECT}:comments` });

export async function allowed(userId: string) {
  await talkable(userId);
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
           case when c.hidden_at is null or ${isModerator} or c.author_user_id = ${viewer}::uuid
                then c.suggestion end as suggestion,
           c.accepted_revision,
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
  suggestion = null,
}: {
  author: Author;
  number: number;
  body: string;
  parentId: string | null;
  anchor: Anchor | null;
  suggestion?: v.InferOutput<typeof SuggestionSchema> | null;
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
  const stored =
    suggestion == null
      ? null
      : await suggestionFor(found, anchor, parentId, suggestion);
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
                           revision_n, body_md, anchor, suggestion)
      select t.id, (select id from parent), ${author.userId}, ${author.keyId}, ${author.agentName},
             ${author.agentModel}, ${found.head},
             ${body}, coalesce(${anchor == null ? null : JSON.stringify(anchor)}::jsonb, (select anchor from parent)),
             ${stored == null ? null : JSON.stringify(stored)}::jsonb
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
                 || array(select f.user_id from profile f where f.handle = any(${sql.param(mentionsOf(body))}::text[]))
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

async function headOf(proposalId: string) {
  const { rows } = await db.execute<{
    n: number;
    payload: { ops?: Array<Op> } & Record<string, unknown>;
    diff: { rows?: Array<DiffRow> } | null;
  }>(sql`
    select r.n, r.payload, r.diff from proposal_revision r
    where r.proposal_id = ${proposalId}
    order by r.n desc limit 1
  `);
  const head = rows[0];
  if (head == null) {
    error(409, "This proposal has no revision yet.");
  }
  return head;
}

async function suggestionFor(
  found: { id: string; author_user_id: string | null },
  anchor: Anchor | null,
  parentId: string | null,
  suggestion: v.InferOutput<typeof SuggestionSchema>,
) {
  if (anchor == null || anchor.kind !== "row" || parentId != null) {
    error(400, "Suggest a change on a changed row, in a new comment.");
  }
  const head = await headOf(found.id);
  if (anchor.revision !== head.n) {
    error(409, "Suggest changes on the latest revision.");
  }
  const ops = head.payload.ops ?? [];
  const row = (head.diff?.rows ?? []).find(
    (item) => item.table === anchor.table && same(item.key, anchor.key),
  );
  const match = row == null ? null : suggestable(ops, row);
  if (match == null) {
    error(400, "That change can't take a suggestion.");
  }
  const before: Record<string, string> = {};
  for (const [field, value] of Object.entries(suggestion.values)) {
    const current = match.fields[field];
    if (current == null) {
      error(400, `A suggestion can't change ${field} here.`);
    }
    if (value.trim() === "") {
      error(400, "A suggested value can't be empty.");
    }
    before[field] = current;
  }
  if (Object.entries(suggestion.values).every(([f, x]) => before[f] === x)) {
    error(400, "The suggestion matches the current value.");
  }
  return { op: match.op, values: suggestion.values, before };
}

export async function acceptSuggestion({
  userId,
  number,
  id,
}: {
  userId: string;
  number: number;
  id: string;
}) {
  const { rows } = await db.execute<{
    proposal_id: string;
    author_user_id: string | null;
    state: string;
    busy: boolean;
    revision_n: number | null;
    suggestion: { op: number; values: Record<string, string> } | null;
    accepted_revision: number | null;
    hidden: boolean;
    suggester: string | null;
  }>(sql`
    select p.id as proposal_id, p.author_user_id, p.state::text as state,
           exists (select from proposal_job j where j.proposal_id = p.id and j.status in ('queued', 'running')) as busy,
           c.revision_n, c.suggestion, c.accepted_revision, c.hidden_at is not null as hidden,
           coalesce(c.agent_name, u.name) as suggester
    from comment c
    join thread t on t.id = c.thread_id
    join proposal p on p.id = t.proposal_id
    left join users u on u.id = c.author_user_id
    where c.id = ${id} and p.number = ${number}
  `);
  const found = rows[0];
  if (found?.suggestion == null || found.hidden) {
    error(404, "No suggestion has that ID on this proposal.");
  }
  if (found.author_user_id !== userId) {
    error(403, "Only the proposal's author can accept a suggestion.");
  }
  if (found.accepted_revision != null) {
    error(409, "This suggestion is already accepted.");
  }
  if (!["open", "changes_requested"].includes(found.state)) {
    error(409, `A ${found.state} proposal can't take a suggestion.`);
  }
  if (found.busy) {
    error(409, "A job on this proposal is still running.");
  }
  const head = await headOf(found.proposal_id);
  if (found.revision_n !== head.n) {
    error(409, "This suggestion is for an older revision.");
  }
  const ops = [...(head.payload.ops ?? [])];
  const target = ops.at(found.suggestion.op);
  if (target == null || !isRecord(target.values)) {
    error(409, "The change this suggestion edits is gone.");
  }
  ops[found.suggestion.op] = {
    ...target,
    values: { ...target.values, ...found.suggestion.values },
  };
  const n = head.n + 1;
  const payload = {
    ...head.payload,
    ops,
    note: `Accepted a suggestion from ${found.suggester ?? "a reviewer"}`,
  };
  await db.transaction(async (tx) => {
    const { rows: claimed } = await tx.execute<{ id: string }>(sql`
      update comment set accepted_revision = ${n}
      where id = ${id} and accepted_revision is null
      returning id
    `);
    if (claimed.length === 0) {
      error(409, "This suggestion is already accepted.");
    }
    await tx.execute(sql`
      insert into proposal_revision (proposal_id, n, payload)
      values (${found.proposal_id}, ${n}, ${JSON.stringify(payload)}::jsonb)
    `);
    await tx.execute(sql`
      update proposal set updated_at = now() where id = ${found.proposal_id}
    `);
    await tx.execute(sql`
      insert into proposal_job (proposal_id, revision_n, kind, requested_by)
      values (${found.proposal_id}, ${n}, 'submit', ${userId})
    `);
  });
  await db.execute(
    sql`select pg_notify('proposal_job', json_build_object('kind', 'queued')::text)`,
  );
  return n;
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
