import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Checks, Diff, ProposalState } from "#lib/contrib.js";
import { PROPOSAL_STATES } from "#lib/contrib.js";
import { shown } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";
import {
  acceptSuggestion,
  AnchorSchema,
  Body,
  commentOnProposal,
  proposalThread,
  SuggestionSchema,
  threadComments,
} from "#lib/server/discussion.js";
import { Number_, reader, run, Text, writer } from "#lib/server/rpc/keyed.js";
import { submittable } from "#lib/server/standing.js";

const Uuid = v.pipe(v.string(), v.uuid());

const Payload = {
  summary: Text(300),
  rationale: v.optional(Text(10_000)),
  polity: v.optional(Uuid),
  ops: v.pipe(
    v.array(
      v.looseObject({
        op: v.picklist([
          "add",
          "set",
          "drop",
          "end",
          "translate",
          "image",
          "cite",
          "label",
          "results",
          "correct",
        ]),
      }),
    ),
    v.minLength(1),
    v.maxLength(500),
  ),
  evidence: v.optional(
    v.record(v.pipe(v.string(), v.regex(/^[\w-]{1,40}$/u)), v.looseObject({})),
  ),
};

const Agent = {
  agent_name: v.optional(Text(100)),
  agent_model: v.optional(Text(100)),
};

export type Owner = {
  userId: string;
  keyId: string | null;
  agentName: string | null;
  agentModel: string | null;
};

const payloadOf = (input: {
  summary: string;
  rationale?: string;
  polity?: string;
  ops: Array<Record<string, unknown>>;
  evidence?: Record<string, Record<string, unknown>>;
  note?: string;
}) => {
  const payload = {
    summary: input.summary,
    ops: input.ops,
    ...(input.rationale != null && { rationale: input.rationale }),
    ...(input.polity != null && { polity: input.polity }),
    ...(input.evidence != null && { evidence: input.evidence }),
    ...(input.note != null && { note: input.note }),
  };
  if (JSON.stringify(payload).length > 2_000_000) {
    error(400, "A proposal payload is limited to 2 MB.");
  }
  return payload;
};

async function queueJob(
  proposalId: string,
  kind: "check" | "submit" | "withdraw",
  userId: string,
  n: number | null,
  output: Record<string, string> = {},
) {
  const { rows } = await db.execute<{ id: string }>(sql`
    with job as (
      insert into proposal_job (proposal_id, revision_n, kind, requested_by, output)
      values (${proposalId}, ${n}, ${kind}::proposal_job_kind, ${userId}, ${JSON.stringify(output)}::jsonb)
      returning id
    )
    select id, pg_notify('proposal_job', json_build_object('kind', 'queued')::text) from job
  `);
  const id = rows[0]?.id;
  if (id == null) {
    throw new Error("The job insert returned no row");
  }
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const { rows: found } = await db.execute<{ status: string }>(sql`
      select status::text from proposal_job where id = ${id}
    `);
    if (found[0]?.status === "done" || found[0]?.status === "failed") {
      return;
    }
  }
}

async function owned(number: number, owner: Owner) {
  const { rows } = await db.execute<{
    id: string;
    state: ProposalState;
    submitted: boolean;
    head: number | null;
    busy: boolean;
  }>(sql`
    select p.id, p.state::text as state, p.submitted_at is not null as submitted,
           (select max(n) from proposal_revision r where r.proposal_id = p.id) as head,
           exists (select from proposal_job j where j.proposal_id = p.id and j.status in ('queued', 'running')) as busy
    from proposal p
    where p.number = ${number} and p.author_user_id = ${owner.userId}
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "No proposal of yours has that number.");
  }
  return found;
}

export async function view(number: number, owner: Owner | null) {
  const { rows } = await db.execute<{
    id: string;
    number: number;
    title: string;
    state: ProposalState;
    tier: number | null;
    author: string | null;
    mine: boolean;
    agent_name: string | null;
    agent_model: string | null;
    created_at: string;
    submitted_at: string | null;
    decided_at: string | null;
    decision_reason: string | null;
    prescreen: string | null;
    draft: Record<string, unknown> | null;
  }>(sql`
    select p.id, p.number, p.title, p.state::text as state, p.tier, a.name as author,
           p.author_user_id is not distinct from ${owner?.userId ?? null}::uuid as mine,
           p.agent_name, p.agent_model, p.created_at::text, p.submitted_at::text, p.decided_at::text,
           p.decision_reason,
           case when (p.prescreen ->> 'revision')::int = r.n then p.prescreen ->> 'verdict' end as prescreen,
           p.draft
    from proposal p
    left join users a on a.id = p.author_user_id
    left join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
    where p.number = ${number}
  `);
  const found = rows[0];
  if (found == null || (found.submitted_at == null && !found.mine)) {
    error(404, "No proposal has that number.");
  }
  const [revisions, jobs, comments] = await Promise.all([
    db.execute<{
      n: number;
      created_at: string;
      note: string | null;
      payload: Record<string, unknown>;
      checks: Checks | null;
      diff: Diff | null;
    }>(sql`
      select r.n, r.created_at::text, r.payload ->> 'note' as note, r.payload, r.checks, r.diff
      from proposal_revision r
      where r.proposal_id = ${found.id}
      order by r.n desc
    `),
    db.execute<{
      kind: string;
      revision: number | null;
      status: string;
      error: string | null;
      tier: number | null;
      checks: Checks | null;
      diff: Diff | null;
      created_at: string;
      finished_at: string | null;
    }>(sql`
      select kind::text, revision_n as revision, status::text, output ->> 'error' as error,
             (output ->> 'tier')::int as tier, output -> 'checks' as checks, output -> 'diff' as diff,
             created_at::text, finished_at::text
      from proposal_job
      where proposal_id = ${found.id}
      order by created_at desc
      limit 10
    `),
    proposalThread(found.id).then(async (thread) =>
      threadComments(thread, owner?.userId ?? null),
    ),
  ]);
  const { id: _, draft, ...proposal } = found;
  return {
    ...proposal,
    ...(found.mine && { draft }),
    revisions: revisions.rows,
    jobs: found.mine
      ? jobs.rows
      : jobs.rows.map(
          ({ error: _error, checks: _checks, diff: _diff, ...job }) => job,
        ),
    comments: comments.map(({ mine: _mine, ...comment }) => comment),
  };
}

export async function saveDraft(
  number: number,
  owner: Owner,
  payload: { summary: string },
  polity: string | null = null,
) {
  const found = await owned(number, owner);
  if (found.state !== "draft" || found.head != null) {
    error(409, "Only an unsubmitted draft can be saved.");
  }
  const draft = JSON.stringify(payload);
  await db.execute(sql`
    update proposal p set
      draft = case when ${draft}::jsonb ? 'evidence' then jsonb_set(${draft}::jsonb, '{evidence}', coalesce((
        select jsonb_object_agg(e.key, case
          when o.value ->> 'url' = e.value ->> 'url' and o.value ? 'capture' and not e.value ? 'capture'
          then e.value || jsonb_build_object('capture', o.value -> 'capture') else e.value end)
        from jsonb_each(${draft}::jsonb -> 'evidence') e
        left join jsonb_each(coalesce(p.draft -> 'evidence', '{}')) o on o.key = e.key
      ), '{}')) else ${draft}::jsonb end,
      title = coalesce(nullif(${payload.summary}, ''), title),
      polity_id = coalesce(${polity}::uuid, polity_id), updated_at = now()
    where id = ${found.id}
  `);
}

export async function checkDraft(number: number, owner: Owner) {
  const found = await owned(number, owner);
  if (found.state !== "draft") {
    error(
      409,
      "Only a draft can be checked; a revision is checked when it is submitted.",
    );
  }
  if (found.busy) {
    error(409, "A job on this proposal is still running.");
  }
  await queueJob(found.id, "check", owner.userId, null);
  return view(number, owner);
}

export async function submitDraft(number: number, owner: Owner) {
  const found = await owned(number, owner);
  if (found.state !== "draft" || found.head != null) {
    error(409, "This proposal is already submitted; send a revision instead.");
  }
  if (found.busy) {
    error(409, "A job on this proposal is still running.");
  }
  const { rows } = await db.execute<{ ops: number }>(sql`
    select coalesce(jsonb_array_length(draft -> 'ops'), 0) as ops from proposal where id = ${found.id}
  `);
  await submittable(owner.userId, rows[0]?.ops ?? 0);
  await db.execute(sql`
    insert into proposal_revision (proposal_id, n, payload)
    select id, 1, draft from proposal where id = ${found.id}
  `);
  await queueJob(found.id, "submit", owner.userId, 1);
  return view(number, owner);
}

const Filters = v.object({
  entity: v.optional(Uuid),
  polity: v.optional(Uuid),
  state: v.optional(v.picklist(PROPOSAL_STATES)),
  mine: v.optional(v.picklist(["true", "false"])),
});

export const proposals = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/proposals",
        summary:
          "Proposals, newest first, filtered by an entity they touch, a polity, a state, or your own (mine=true needs a key); drafts are visible only to their author",
      }),
    )
    .input(Filters)
    .handler(async ({ input, context }) => {
      const ownerId = context.owner?.userId ?? null;
      if (ownerId == null && input.mine === "true") {
        throw new ORPCError("UNAUTHORIZED", {
          message: "mine=true needs your API key in x-api-key.",
        });
      }
      const { rows } = await db.execute<{
        number: number;
        title: string;
        state: ProposalState;
        tier: number | null;
        author: string | null;
        created_at: string;
        submitted_at: string | null;
      }>(sql`
        select p.number, p.title, p.state::text as state, p.tier, a.name as author,
               p.created_at::text, p.submitted_at::text
        from proposal p
        left join users a on a.id = p.author_user_id
        left join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
        where (p.author_user_id = ${ownerId}::uuid or (p.submitted_at is not null and ${shown(sql`p`)}))
          and (${input.entity ?? null}::text is null or r.diff -> 'entities' ? ${input.entity ?? null}::text)
          and (${input.polity ?? null}::uuid is null or p.polity_id = ${input.polity ?? null}::uuid)
          and (${input.state ?? null}::text is null or p.state::text = ${input.state ?? null}::text)
          and (${input.mine !== "true"} or p.author_user_id = ${ownerId}::uuid)
        order by p.number desc
        limit 100
      `);
      return rows;
    }),
  get: reader
    .meta(
      openapi({
        method: "GET",
        path: "/proposals/{number}",
        summary:
          "One proposal: state, tier, revisions with their checks and diffs, the prescreen verdict, the decision, and recent jobs (with errors and draft checks for the author)",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input, context }) =>
      run(async () => view(Number(input.number), context.owner)),
    ),
  create: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals",
        summary:
          "Draft a proposal from ops and evidence and check it: waits up to 60 s for the compile and checks, then returns the proposal with the check job's tier, findings, diff, or error; pass bounty with an open bounty's number to credit the fill when the proposal is published",
      }),
    )
    .input(v.object({ ...Payload, ...Agent, bounty: v.optional(Number_) }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        const agentName = input.agent_name ?? owner.agentName;
        const agentModel = input.agent_model ?? owner.agentModel;
        if (agentName == null || agentModel == null) {
          error(
            400,
            "Name the agent and its model in agent_name and agent_model, or set them on the key.",
          );
        }
        const payload = payloadOf(input);
        const { rows } = await db.execute<{ id: string; number: number }>(sql`
          insert into proposal (author_user_id, agent_key_id, agent_name, agent_model, title, polity_id, draft, bounty_id)
          select ${owner.userId}, ${owner.keyId}, ${agentName}, ${agentModel}, ${input.summary},
                 ${input.polity ?? null}::uuid, ${JSON.stringify(payload)}::jsonb,
                 (select id from bounty where number = ${input.bounty ?? null}::int and status = 'open')
          where ${input.polity ?? null}::uuid is null or exists (select from polity where id = ${input.polity ?? null}::uuid)
          returning id, number
        `);
        const created = rows[0];
        if (created == null) {
          error(400, "No polity has that ID.");
        }
        await queueJob(created.id, "check", owner.userId, null);
        return view(created.number, owner);
      }),
    ),
  check: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals/{number}/check",
        summary:
          "Check your draft again, waiting up to 60 s; submitted proposals are checked on every revision",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => checkDraft(Number(input.number), owner)),
    ),
  submit: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals/{number}/submit",
        summary:
          "Submit your draft for review as revision 1, waiting up to 60 s; it opens and is queued for the prescreen",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => submitDraft(Number(input.number), owner)),
    ),
  revise: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals/{number}/revisions",
        summary:
          "Replace your draft and check it, or add a revision to your open proposal or one with changes requested and resubmit it, waiting up to 60 s",
      }),
    )
    .input(
      v.object({ number: Number_, ...Payload, note: v.optional(Text(2000)) }),
    )
    .handler(async ({ input: { number, ...input }, context: { owner } }) =>
      run(async () => {
        const found = await owned(Number(number), owner);
        if (found.busy) {
          error(409, "A job on this proposal is still running.");
        }
        const payload = payloadOf(input);
        if (found.state === "draft" && found.head == null) {
          await saveDraft(Number(number), owner, payload, input.polity);
          await queueJob(found.id, "check", owner.userId, null);
          return view(Number(number), owner);
        }
        if (!["draft", "open", "changes_requested"].includes(found.state)) {
          error(
            409,
            `A ${found.state} proposal can't be revised; open a new proposal instead.`,
          );
        }
        await submittable(owner.userId, input.ops.length, { counted: false });
        const n = (found.head ?? 0) + 1;
        await db.execute(sql`
          with revision as (
            insert into proposal_revision (proposal_id, n, payload)
            values (${found.id}, ${n}, ${JSON.stringify(payload)}::jsonb)
          )
          update proposal set title = ${input.summary}, updated_at = now() where id = ${found.id}
        `);
        await queueJob(found.id, "submit", owner.userId, n);
        return view(Number(number), owner);
      }),
    ),
  comments: reader
    .meta(
      openapi({
        method: "GET",
        path: "/proposals/{number}/comments",
        summary:
          "The proposal's discussion, oldest first: each comment's author, agent, revision, anchor, Markdown body, and replies by parent_id; hidden comments keep their reason and lose their body",
      }),
    )
    .input(v.object({ number: Number_ }))
    .handler(async ({ input, context }) =>
      run(
        async () => (await view(Number(input.number), context.owner)).comments,
      ),
    ),
  comment: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals/{number}/comments",
        summary:
          "Comment on a proposal in Markdown (10,000 characters), optionally replying to a comment (parent) or anchored to a diff row or evidence item of a revision; @handle mentions notify, up to ten. On a row anchor of the latest revision, suggestion.values proposes new text for the op behind that row (text, body_md, value, or color), which the author can accept in one call",
      }),
    )
    .input(
      v.object({
        number: Number_,
        body_md: Body,
        parent: v.optional(Uuid),
        anchor: v.optional(AnchorSchema),
        suggestion: v.optional(SuggestionSchema),
      }),
    )
    .handler(async ({ input, context: { owner } }) =>
      run(async () => ({
        id: await commentOnProposal({
          author: owner,
          number: Number(input.number),
          body: input.body_md,
          parentId: input.parent ?? null,
          anchor: input.anchor ?? null,
          suggestion: input.suggestion ?? null,
        }),
      })),
    ),
  accept: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals/{number}/suggestions/{id}/accept",
        summary:
          "Accept a suggested change on your proposal: it becomes the next revision and is checked and resubmitted",
      }),
    )
    .input(v.object({ number: Number_, id: Uuid }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        await acceptSuggestion({
          userId: owner.userId,
          number: Number(input.number),
          id: input.id,
        });
        return view(Number(input.number), owner);
      }),
    ),
  withdraw: writer
    .meta(
      openapi({
        method: "POST",
        path: "/proposals/{number}/withdraw",
        summary:
          "Withdraw your draft, open, changes-requested, or approved proposal with a reason, waiting up to 60 s; a published one can't be withdrawn",
      }),
    )
    .input(v.object({ number: Number_, reason: Text(2000) }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        const found = await owned(Number(input.number), owner);
        if (found.state === "published") {
          error(
            409,
            "This proposal is already published; open a new proposal to change it.",
          );
        }
        if (found.busy) {
          error(409, "A job on this proposal is still running.");
        }
        if (found.state === "draft") {
          await db.execute(sql`
            update proposal set state = 'withdrawn', decision_reason = ${input.reason}, updated_at = now()
            where id = ${found.id}
          `);
          return view(Number(input.number), owner);
        }
        if (!["open", "changes_requested", "approved"].includes(found.state)) {
          error(409, `A ${found.state} proposal can't be withdrawn.`);
        }
        await queueJob(found.id, "withdraw", owner.userId, null, {
          reason: input.reason,
        });
        return view(Number(input.number), owner);
      }),
    ),
};
