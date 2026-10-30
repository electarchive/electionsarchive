import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Checks, Diff, ProposalState } from "#lib/contrib.js";
import type { keyOwner } from "#lib/server/rpc/keyed.js";
import { db } from "#lib/server/db/index.js";
import { Number_, reader, run, Text, writer } from "#lib/server/rpc/keyed.js";

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

type Owner = NonNullable<Awaited<ReturnType<typeof keyOwner>>>;

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

async function view(number: number, owner: Owner | null) {
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
  const [revisions, jobs] = await Promise.all([
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
  };
}

const Filters = v.object({
  entity: v.optional(Uuid),
  polity: v.optional(Uuid),
  state: v.optional(
    v.picklist([
      "draft",
      "open",
      "changes_requested",
      "approved",
      "published",
      "rejected",
      "withdrawn",
      "reverted",
    ]),
  ),
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
        where (p.submitted_at is not null or p.author_user_id = ${ownerId}::uuid)
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
          "Draft a proposal from ops and evidence and check it: waits up to 60 s for the compile and checks, then returns the proposal with the check job's tier, findings, diff, or error",
      }),
    )
    .input(v.object({ ...Payload, ...Agent }))
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
          insert into proposal (author_user_id, agent_key_id, agent_name, agent_model, title, polity_id, draft)
          select ${owner.userId}, ${owner.keyId}, ${agentName}, ${agentModel}, ${input.summary},
                 ${input.polity ?? null}::uuid, ${JSON.stringify(payload)}::jsonb
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
      run(async () => {
        const found = await owned(Number(input.number), owner);
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
        return view(Number(input.number), owner);
      }),
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
      run(async () => {
        const found = await owned(Number(input.number), owner);
        if (found.state !== "draft" || found.head != null) {
          error(
            409,
            "This proposal is already submitted; send a revision instead.",
          );
        }
        if (found.busy) {
          error(409, "A job on this proposal is still running.");
        }
        await db.execute(sql`
          insert into proposal_revision (proposal_id, n, payload)
          select id, 1, draft from proposal where id = ${found.id}
        `);
        await queueJob(found.id, "submit", owner.userId, 1);
        return view(Number(input.number), owner);
      }),
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
          await db.execute(sql`
            update proposal set draft = ${JSON.stringify(payload)}::jsonb, title = ${input.summary},
                   polity_id = coalesce(${input.polity ?? null}::uuid, polity_id), updated_at = now()
            where id = ${found.id}
          `);
          await queueJob(found.id, "check", owner.userId, null);
          return view(Number(number), owner);
        }
        if (!["draft", "open", "changes_requested"].includes(found.state)) {
          error(
            409,
            `A ${found.state} proposal can't be revised; open a new proposal instead.`,
          );
        }
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
