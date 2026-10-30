import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import type {
  Change,
  Checks,
  Diff,
  Editable,
  ProposalState,
} from "#lib/contrib.js";
import { schemes } from "#lib/contrib.js";
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
  const [names, notes, ids, colors, kind, sources, open] = await Promise.all([
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
    db.execute<Editable["notes"][number]>(sql`
      select n.id, n.kind::text as kind, n.body_md, n.lang, c.published_at::text as changed
      from note n
      left join changeset c on c.id = n.changeset_id
      where n.entity_id = ${entityId} and n.kind <> 'retired'
      order by n.recorded_from, n.id
    `),
    db.execute<Editable["ids"][number]>(sql`
      select scheme, value
      from external_id
      where entity_id = ${entityId} and scheme = any(${`{${schemes.join(",")}}`}::text[])
      order by scheme, value
    `),
    db.execute<Editable["colors"][number]>(sql`
      select pc.color, pc.valid_from::text, pc.valid_from_precision::text as valid_from_precision,
             pc.valid_to::text, pc.valid_to_precision::text as valid_to_precision, s.title as source
      from party_color pc
      left join source s on s.id = pc.source_id
      where pc.party_id = ${entityId}
      order by pc.valid_from nulls first
    `),
    db.execute<{ party: boolean; person: boolean }>(sql`
      select exists (select from party where id = ${entityId}) as party,
             exists (select from person where id = ${entityId}) as person
    `),
    db.execute<Editable["sources"][number]>(sql`
      select s.id, s.title, s.publisher
      from source s
      where s.id in (
        select source_id from name where entity_id = ${entityId} and source_id is not null
        union
        select source_id from citation where entity_id = ${entityId}
        union
        select source_id from party_color where party_id = ${entityId} and source_id is not null
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
  return {
    names: names.rows,
    notes: notes.rows,
    ids: ids.rows,
    kind:
      kind.rows[0]?.party === true
        ? "party"
        : kind.rows[0]?.person === true
          ? "person"
          : null,
    colors: colors.rows,
    sources: sources.rows,
    open: open.rows,
  };
}

export async function propose({
  userId,
  entityId,
  changes,
  evidence,
  quote,
  summary,
}: {
  userId: string;
  entityId: string;
  changes: Array<Change>;
  evidence:
    | { type: "source"; sourceId: string }
    | {
        type: "url";
        url: string;
        title: string;
        publisher: string;
        kind: string;
      }
    | {
        type: "upload";
        uploadId: string;
        page: number | null;
        region: [number, number, number, number] | null;
        title: string;
        publisher: string;
        kind: string;
      }
    | null;
  quote: string;
  summary: string;
}) {
  if (
    evidence == null &&
    changes.some(
      (change) => change.kind === "name" || change.kind.startsWith("color_"),
    )
  ) {
    error(400, "A name or color change needs a source.");
  }
  if (changes.filter((change) => change.kind.startsWith("color_")).length > 1) {
    error(400, "A proposal can change one color period.");
  }
  const nameIds = changes.flatMap((change) =>
    change.kind === "name" ? [change.id] : [],
  );
  const noteIds = changes.flatMap((change) =>
    change.kind === "note_set" || change.kind === "note_drop"
      ? [change.id]
      : [],
  );
  const dropped = changes.flatMap((change) =>
    change.kind === "id_drop" ? [`${change.scheme}:${change.value}`] : [],
  );
  const sourceId = evidence?.type === "source" ? evidence.sourceId : null;
  const uploadId = evidence?.type === "upload" ? evidence.uploadId : null;
  const { rows } = await db.execute<{ ok: boolean }>(sql`
    select (select count(*) from name where id = any(${`{${nameIds.join(",")}}`}::uuid[]) and entity_id = ${entityId})
             = ${nameIds.length}
       and (select count(*) from note
            where id = any(${`{${noteIds.join(",")}}`}::uuid[]) and entity_id = ${entityId} and kind <> 'retired')
             = ${noteIds.length}
       and (select count(*) from external_id
            where scheme || ':' || value = any(${`{${dropped.join(",")}}`}::text[]) and entity_id = ${entityId})
             = ${dropped.length}
       and (${sourceId}::uuid is null or exists (select from source where id = ${sourceId}))
       and (${uploadId}::uuid is null or exists (
         select from upload where id = ${uploadId} and owner_user_id = ${userId} and status = 'ready'
       )) as ok
  `);
  if (rows[0]?.ok !== true) {
    error(
      400,
      "Some of these changes, or the source, can't be used. They may have been removed, or the upload may still be processing.",
    );
  }
  const cited = (() => {
    switch (evidence?.type) {
      case undefined: {
        return null;
      }
      case "source": {
        return { source: evidence.sourceId };
      }
      case "url": {
        const { type: _, ...rest } = evidence;
        return rest;
      }
      case "upload": {
        const { type: _, uploadId: upload, page, region, ...rest } = evidence;
        return {
          upload,
          ...rest,
          ...(page != null && { page }),
          ...(region != null && { region }),
        };
      }
    }
  })();
  const op = (change: Change) => {
    switch (change.kind) {
      case "name": {
        return {
          op: "set",
          table: "name",
          id: change.id,
          values: { text: change.text },
        };
      }
      case "note_add": {
        return {
          op: "add",
          table: "note",
          values: {
            entity_id: entityId,
            kind: change.noteKind,
            lang: change.lang,
            body_md: change.body,
          },
        };
      }
      case "note_set": {
        return {
          op: "set",
          table: "note",
          id: change.id,
          values: { body_md: change.body },
        };
      }
      case "note_drop": {
        return { op: "drop", table: "note", id: change.id };
      }
      case "id_add": {
        return {
          op: "add",
          table: "external_id",
          values: {
            entity_id: entityId,
            scheme: change.scheme,
            value: change.value,
          },
        };
      }
      case "id_drop": {
        return {
          op: "drop",
          table: "external_id",
          key: { scheme: change.scheme, value: change.value },
        };
      }
      case "color_set": {
        return {
          op: "set",
          table: "party_color",
          key: { party_id: entityId, valid_from: change.from },
          values: { color: change.color },
        };
      }
      case "color_end": {
        return {
          op: "end",
          table: "party_color",
          key: { party_id: entityId, valid_from: change.from },
          values: { valid_to: change.to },
        };
      }
      case "color_add": {
        return {
          op: "add",
          table: "party_color",
          values: {
            party_id: entityId,
            color: change.color,
            valid_from: change.from,
            valid_to: change.to,
          },
        };
      }
    }
  };
  const payload = {
    summary,
    ops: changes.map((change) => ({
      ...op(change),
      ...(cited != null && { evidence: ["e1"] }),
    })),
    ...(cited != null && {
      evidence: { e1: { ...cited, ...(quote !== "" && { quote }) } },
    }),
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
        evidence?: Record<
          string,
          {
            source?: string;
            upload?: string;
            page?: number;
            region?: [number, number, number, number];
            quote?: string;
          }
        >;
      };
      diff: Diff | null;
      checks: Checks | null;
      created_at: string;
      sources: Array<{
        key: string;
        title: string;
        url: string | null;
        captured: string | null;
        wayback: string | null;
        scan: {
          upload: string;
          page: number;
          width: number;
          height: number;
          region: [number, number, number, number] | null;
          shared: boolean;
          owner: string | null;
        } | null;
      }> | null;
      titles: Record<string, string> | null;
    }>(sql`
      select r.n, r.payload, r.diff, r.checks, r.created_at::text,
             (select json_agg(json_build_object(
                       'key', e.key,
                       'title', coalesce(s.title, e.value ->> 'title'),
                       'url', coalesce(s.url, e.value ->> 'url'),
                       'captured', a.captured_at,
                       'wayback', a.wayback_url,
                       'scan', case when p.upload_id is not null then json_build_object(
                         'upload', u.id, 'page', p.page_number, 'width', p.width, 'height', p.height,
                         'region', e.value -> 'region', 'shared', u.public_record, 'owner', u.owner_user_id
                       ) end
                     ) order by e.key)
              from jsonb_each(r.payload -> 'evidence') e
              left join source s on s.id::text = e.value ->> 'source'
              left join archive_capture a on a.id::text = e.value ->> 'capture' and a.status = 'captured'
              left join upload u on u.id::text = e.value ->> 'upload' and u.status = 'ready'
              left join upload_page p on p.upload_id = u.id and p.page_number::text = e.value ->> 'page'
              where s.id is not null or e.value ? 'url' or u.id is not null) as sources,
             (select json_object_agg(s.id, s.title)
              from source s
              where s.id::text in (
                select jsonb_array_elements_text(x -> 'fields' -> 'source_id')
                from jsonb_array_elements(r.diff -> 'rows') x
                where x -> 'fields' ? 'source_id'
              )) as titles
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
           (select count(*) from jsonb_array_elements(r.diff -> 'rows') x where x ->> 'table' not in ('citation', 'source', 'source_artifact', 'source_page'))::int as changes,
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
