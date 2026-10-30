import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import type { Checks, Diff, Editable, ProposalState } from "#lib/contrib.js";
import { db } from "#lib/server/db/index.js";

const notify = sql`select pg_notify('proposal_job', json_build_object('kind', 'queued')::text)`;

export async function roles(userId: string | undefined) {
  if (userId == null) {
    return { admin: false, reviewer: false };
  }
  const { rows } = await db.execute<{ role: "admin" | "reviewer" }>(sql`
    select role from user_role where user_id = ${userId}
  `);
  return {
    admin: rows.some((row) => row.role === "admin"),
    reviewer: rows.some((row) => row.role === "reviewer"),
  };
}

export async function editable(entityId: string): Promise<Editable> {
  const [names, sources, open] = await Promise.all([
    db.execute<Editable["names"][number]>(sql`
      with shown as (
        select id from name
        where entity_id = ${entityId} and kind in ('official', 'english')
        order by coalesce(valid @> current_date, false) desc,
          coalesce(valid @> current_date and not (lower_inf(valid) and upper_inf(valid)), false) desc,
          (kind = 'english' and script = 'Latn') desc, is_preferred desc, (lang = 'en') desc,
          (kind = 'official') desc, recorded_from desc, id
        limit 1
      )
      select n.id, n.text, n.kind::text as kind, n.lang, n.script, n.is_preferred,
             n.id in (select id from shown) as shown,
             n.source_id, s.title as source, c.published_at::text as changed
      from name n
      left join source s on s.id = n.source_id
      left join changeset c on c.id = n.changeset_id
      where n.entity_id = ${entityId}
      order by shown desc, n.is_preferred desc, n.kind, n.text
    `),
    db.execute<Editable["sources"][number]>(sql`
      select s.id, s.title, s.publisher
      from source s
      where s.id in (
        select source_id from name where entity_id = ${entityId} and source_id is not null
        union
        select source_id from citation where entity_id = ${entityId}
      )
      order by s.title
      limit 50
    `),
    db.execute<Editable["open"][number]>(sql`
      select p.number, p.title, p.state::text as state
      from proposal p
      join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
      where p.state in ('open', 'approved', 'changes_requested')
        and r.diff -> 'entities' ? ${entityId}
      order by p.number
    `),
  ]);
  return { names: names.rows, sources: sources.rows, open: open.rows };
}

export async function propose({
  userId,
  entityId,
  nameId,
  text,
  sourceId,
  quote,
  summary,
}: {
  userId: string;
  entityId: string;
  nameId: string;
  text: string;
  sourceId: string;
  quote: string;
  summary: string;
}) {
  const { rows } = await db.execute<{ ok: boolean }>(sql`
    select exists (select from name where id = ${nameId} and entity_id = ${entityId})
       and exists (select from source where id = ${sourceId}) as ok
  `);
  if (rows[0]?.ok !== true) {
    error(400, "That name or source no longer exists.");
  }
  const payload = {
    summary,
    ops: [
      {
        op: "set",
        table: "name",
        id: nameId,
        values: { text },
        evidence: ["e1"],
      },
    ],
    evidence: { e1: { source: sourceId, ...(quote !== "" && { quote }) } },
  };
  return db.transaction(async (tx) => {
    const created = await tx.execute<{ id: string; number: number }>(sql`
      insert into proposal (author_user_id, title)
      values (${userId}, ${summary})
      returning id, number
    `);
    const proposal = created.rows[0];
    if (proposal == null) {
      throw new Error("The proposal insert returned no row");
    }
    await tx.execute(sql`
      insert into proposal_revision (proposal_id, n, payload)
      values (${proposal.id}, 1, ${JSON.stringify(payload)}::jsonb)
    `);
    await tx.execute(sql`
      insert into proposal_job (proposal_id, revision_n, kind, requested_by)
      values (${proposal.id}, 1, 'submit', ${userId})
    `);
    await tx.execute(notify);
    return proposal.number;
  });
}

export async function proposal(number: number) {
  const { rows } = await db.execute<{
    id: string;
    number: number;
    title: string;
    state: ProposalState;
    tier: number | null;
    author: string | null;
    created_at: string;
    submitted_at: string | null;
    decided_at: string | null;
    decider: string | null;
    decision_reason: string | null;
    head_changeset_id: string | null;
  }>(sql`
    select p.id, p.number, p.title, p.state::text as state, p.tier, a.name as author,
           p.created_at::text, p.submitted_at::text, p.decided_at::text, d.name as decider,
           p.decision_reason, p.head_changeset_id
    from proposal p
    left join users a on a.id = p.author_user_id
    left join users d on d.id = p.decided_by
    where p.number = ${number}
  `);
  const found = rows[0];
  if (found == null) {
    error(404);
  }
  const [revisions, jobs] = await Promise.all([
    db.execute<{
      n: number;
      payload: {
        summary: string;
        rationale?: string;
        evidence?: Record<string, { source?: string; quote?: string }>;
      };
      diff: Diff | null;
      checks: Checks | null;
      created_at: string;
      sources: Array<{ key: string; id: string; title: string }> | null;
    }>(sql`
      select r.n, r.payload, r.diff, r.checks, r.created_at::text,
             (select json_agg(json_build_object('key', e.key, 'id', s.id, 'title', s.title) order by e.key)
              from jsonb_each(r.payload -> 'evidence') e
              join source s on s.id::text = e.value ->> 'source') as sources
      from proposal_revision r
      where r.proposal_id = ${found.id}
      order by r.n desc
    `),
    db.execute<{
      kind: string;
      status: "queued" | "running" | "done" | "failed";
      error: string | null;
      created_at: string;
    }>(sql`
      select kind::text, status::text, output ->> 'error' as error, created_at::text
      from proposal_job
      where proposal_id = ${found.id}
      order by created_at desc
      limit 20
    `),
  ]);
  return { ...found, revisions: revisions.rows, jobs: jobs.rows };
}

export async function queue() {
  const { rows } = await db.execute<{
    number: number;
    title: string;
    state: ProposalState;
    tier: number | null;
    author: string | null;
    submitted_at: string | null;
    changes: number;
    findings: number;
  }>(sql`
    select p.number, p.title, p.state::text as state, p.tier, a.name as author,
           p.submitted_at::text,
           coalesce(jsonb_array_length(r.diff -> 'rows'), 0)::int as changes,
           coalesce((select sum(value::int) from jsonb_each_text(r.checks -> 'findings')), 0)::int as findings
    from proposal p
    left join users a on a.id = p.author_user_id
    left join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
    where p.state in ('open', 'approved')
    order by p.tier nulls last, p.submitted_at nulls last, p.number
    limit 200
  `);
  return rows;
}

export async function decide({
  userId,
  number,
  verb,
  reason,
}: {
  userId: string;
  number: number;
  verb: "approve" | "reject";
  reason: string;
}) {
  const [allowed, found] = await Promise.all([
    roles(userId),
    db.execute<{ id: string; state: ProposalState; tier: number | null }>(sql`
      select id, state::text as state, tier from proposal where number = ${number}
    `),
  ]);
  const target = found.rows[0];
  if (target == null) {
    error(404);
  }
  if (!(allowed.admin || (allowed.reviewer && target.tier === 1))) {
    error(403, "Only an admin can decide this proposal.");
  }
  if (target.state !== "open") {
    error(409, "This proposal is no longer open.");
  }
  if (verb === "reject" && reason === "") {
    error(400, "A rejection needs a reason.");
  }
  await db.transaction(async (tx) => {
    await tx.execute(sql`
      update proposal
      set state = ${verb === "approve" ? "approved" : "open"}::proposal_state,
          decision_reason = ${reason === "" ? null : reason}, updated_at = now()
      where id = ${target.id}
    `);
    await tx.execute(sql`
      insert into proposal_job (proposal_id, kind, requested_by, output)
      values (${target.id}, ${verb === "approve" ? "publish" : "reject"}::proposal_job_kind, ${userId},
              ${JSON.stringify(reason === "" ? {} : { reason })}::jsonb)
    `);
    await tx.execute(notify);
  });
}
