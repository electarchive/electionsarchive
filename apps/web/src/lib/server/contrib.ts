import type { SQL } from "drizzle-orm";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import type {
  Change,
  Checks,
  Diff,
  Editable,
  Escalation,
  Prereview,
  Prescreen,
  ProposalState,
} from "#lib/contrib.js";
import { schemes } from "#lib/contrib.js";
import { db } from "#lib/server/db/index.js";

const notify = sql`select pg_notify('proposal_job', json_build_object('kind', 'queued')::text)`;

const escalations = (id: SQL) => sql`
  (
    select array_remove(array[
      case when er.diff is null then 'unchecked' end,
      case when exists (
        select from jsonb_each_text(er.checks -> 'findings') f
        where f.key <> 'scope.owner' and f.value::int > 0
      ) then 'findings' end,
      case when ep.prescreen is null or ep.prescreen ->> 'verdict' <> 'clean'
             or (ep.prescreen ->> 'revision')::int is distinct from er.n then 'prescreen' end,
      case when (select count(*) from proposal q
                 where q.author_user_id = ep.author_user_id and q.state = 'published') < 3 then 'newcomer' end,
      case when exists (
        select from jsonb_array_elements(er.diff -> 'rows') x
        join entity e on e.id = (x ->> 'entity_id')::uuid and e.type in ('polity', 'party')
        left join name n on n.id::text = coalesce(x -> 'key' ->> 'id', x -> 'key' ->> 'name_id')
        where x ->> 'table' = 'name'
          and (n.is_preferred or coalesce(x -> 'fields' -> 'is_preferred' @> 'true', false))
      ) then 'preferred_name' end,
      case when jsonb_array_length(coalesce(er.diff -> 'rows', '[]')) > 500
             or coalesce((er.diff ->> 'truncated')::boolean, false) then 'large' end,
      case when exists (
        select from proposal q
        join proposal_revision qr on qr.proposal_id = q.id and qr.changeset_id = q.head_changeset_id
        where q.id <> ep.id and q.state in ('open', 'changes_requested', 'approved')
          and qr.diff -> 'entities' ?| array(select jsonb_array_elements_text(er.diff -> 'entities'))
      ) then 'conflict' end
    ], null)
    from proposal ep
    left join proposal_revision er on er.proposal_id = ep.id and er.changeset_id = ep.head_changeset_id
    where ep.id = ${id}
  )
`;

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
  const [names, notes, ids, colors, kind, images, sources, citations, open] =
    await Promise.all([
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
        select n.id, n.kind::text as kind, n.body_md, n.lang, md5(n.body_md) as md5,
               c.published_at::text as changed,
               coalesce((
                 select json_agg(json_build_object(
                          'lang', t.lang, 'body', t.body, 'origin', t.origin, 'stale', t.stale_from is not null
                        ) order by t.lang)
                 from translation t
                 where t.source_md5 = md5(n.body_md)
               ), '[]') as translations
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
      db.execute<Editable["images"][number]>(sql`
        select path, license, artist, source_url, valid_from::text, valid_from_precision::text as valid_from_precision,
               valid_to::text, valid_to_precision::text as valid_to_precision
        from party_image
        where party_id = ${entityId}
        union all
        select path, license, artist, source_url, taken_from::text, taken_from_precision::text,
               taken_to::text, taken_to_precision::text
        from person_image
        where person_id = ${entityId}
        order by valid_from nulls first, path
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
      db.execute<Editable["citations"][number]>(sql`
        select s.id, s.title, s.publisher, s.url,
               coalesce(array_agg(c.id::text order by c.id)
                 filter (where ch.pipeline = 'contrib' and c.locator ->> 'op' = 'cite'), '{}') as own
        from citation c
        join source s on s.id = c.source_id
        join changeset ch on ch.id = c.changeset_id
        where c.entity_id = ${entityId}
        group by s.id
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
    images: images.rows,
    sources: sources.rows,
    citations: citations.rows,
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
  const hasDates = (change: Change) =>
    change.kind === "image" && (change.from != null || change.to != null);
  const requiresSource = (change: Change) =>
    hasDates(change) ||
    change.kind === "name" ||
    change.kind === "cite" ||
    change.kind.startsWith("color_") ||
    (change.kind === "translate" && change.origin === "official");
  const canCite = (change: Change) =>
    change.kind === "image"
      ? hasDates(change)
      : change.kind !== "translate" || change.origin === "official";
  if (evidence == null && changes.some((change) => requiresSource(change))) {
    error(
      400,
      "A name, color, logo date, citation, or official translation needs a source.",
    );
  }
  if (
    evidence?.type === "upload" &&
    changes.some((change) => hasDates(change))
  ) {
    error(400, "Link the page that states the logo's dates.");
  }
  if (changes.filter((change) => change.kind.startsWith("color_")).length > 1) {
    error(400, "A proposal can change one color period.");
  }
  if (changes.filter((change) => change.kind === "cite").length > 1) {
    error(400, "A proposal can cite one source.");
  }
  const hasCite = changes.some((change) => change.kind === "cite");
  const uncited = changes.flatMap((change) =>
    change.kind === "cite_drop" ? [change.id] : [],
  );
  const image = changes.find((change) => change.kind === "image");
  if (changes.filter((change) => change.kind === "image").length > 1) {
    error(400, "A proposal can add one image.");
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
  const translated = changes.flatMap((change) =>
    change.kind === "translate" ? [change] : [],
  );
  if (
    translated.some((change) => noteIds.includes(change.note)) ||
    new Set(translated.map((change) => `${change.note}:${change.lang}`))
      .size !== translated.length
  ) {
    error(
      400,
      "Translate a note in a separate proposal from changes to its text, once per language.",
    );
  }
  const seen = translated.map((change) => `${change.note}:${change.md5}`);
  const sourceId = evidence?.type === "source" ? evidence.sourceId : null;
  const uploadId = evidence?.type === "upload" ? evidence.uploadId : null;
  const { rows } = await db.execute<{
    ok: boolean;
    party: boolean;
    held: boolean;
  }>(sql`
    select (select count(*) from name where id = any(${`{${nameIds.join(",")}}`}::uuid[]) and entity_id = ${entityId})
             = ${nameIds.length}
       and (select count(*) from note
            where id = any(${`{${noteIds.join(",")}}`}::uuid[]) and entity_id = ${entityId} and kind <> 'retired')
             = ${noteIds.length}
       and (select count(*) from external_id
            where scheme || ':' || value = any(${`{${dropped.join(",")}}`}::text[]) and entity_id = ${entityId})
             = ${dropped.length}
       and (select count(*) from note
            where id::text || ':' || md5(body_md) = any(${`{${seen.join(",")}}`}::text[])
              and entity_id = ${entityId} and kind <> 'retired')
             = ${new Set(seen).size}
       and (${sourceId}::uuid is null or exists (select from source where id = ${sourceId}))
       and (${uploadId}::uuid is null or exists (
         select from upload where id = ${uploadId} and owner_user_id = ${userId} and status = 'ready'
       ))
       and (${image == null} or exists (select from person where id = ${entityId})
            or exists (select from party where id = ${entityId}))
       and (select count(*) from citation c join changeset ch on ch.id = c.changeset_id
            where c.id = any(${`{${uncited.join(",")}}`}::uuid[]) and c.entity_id = ${entityId}
              and ch.pipeline = 'contrib' and c.locator ->> 'op' = 'cite')
             = ${new Set(uncited).size} as ok,
       exists (select from party where id = ${entityId}) as party,
       ${hasCite} and exists (select from citation where entity_id = ${entityId} and source_id = ${sourceId}::uuid) as held
  `);
  if (rows[0]?.ok !== true) {
    error(
      400,
      "Some of these changes, or the source, can't be used. They may have been removed, or the upload may still be processing.",
    );
  }
  if (rows[0].held) {
    error(400, "This record already cites that source.");
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
      case "translate": {
        return {
          op: "translate",
          of: { table: "note", id: change.note },
          lang: change.lang,
          origin: change.origin,
          body: change.body,
        };
      }
      case "image": {
        const owner = rows[0]?.party === true ? "party" : "person";
        return {
          op: "image",
          table: `${owner}_image`,
          values: {
            [`${owner}_id`]: entityId,
            file: change.file,
            ...(owner === "party" &&
              change.from != null && { valid_from: change.from }),
            ...(owner === "party" &&
              change.to != null && { valid_to: change.to }),
          },
        };
      }
      case "cite": {
        return { op: "cite", entity_id: entityId };
      }
      case "cite_drop": {
        return { op: "drop", table: "citation", id: change.id };
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
  const isCited = cited != null && changes.some((change) => canCite(change));
  const payload = {
    summary,
    ops: changes.map((change) => ({
      ...op(change),
      ...(isCited && canCite(change) && { evidence: ["e1"] }),
    })),
    ...(isCited && {
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
    prescreen: Prescreen | null;
    prereview: Prereview | null;
    prereviewer: string | null;
    escalations: Array<Escalation>;
  }>(sql`
    select p.id, p.number, p.title, p.state::text as state, p.tier, a.name as author,
           p.created_at::text, p.submitted_at::text, p.decided_at::text, d.name as decider,
           p.decision_reason, p.head_changeset_id, p.prescreen, p.prereview, w.name as prereviewer,
           ${escalations(sql`p.id`)} as escalations
    from proposal p
    left join users a on a.id = p.author_user_id
    left join users d on d.id = p.decided_by
    left join users w on w.id::text = p.prereview ->> 'by'
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
      originals: Record<string, { body: string; lang: string }> | null;
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
              )) as titles,
             (select json_object_agg(o.md5, json_build_object('body', o.body_md, 'lang', o.lang))
              from (
                select distinct on (md5(x.body_md)) md5(x.body_md) as md5, x.body_md, x.lang
                from (
                  select id, body_md, lang from note
                  union all
                  select id, body_md, lang from note_history
                ) x
                where x.id = any(array(
                  select (op -> 'of' ->> 'id')::uuid
                  from jsonb_array_elements(r.payload -> 'ops') op
                  where op ->> 'op' = 'translate'
                ))
              ) o) as originals
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
    prescreen: Prescreen["verdict"] | null;
    recommendation: Prereview["recommendation"] | null;
    escalations: Array<Escalation>;
  }>(sql`
    select p.number, p.title, p.state::text as state, p.tier, a.name as author,
           p.submitted_at::text,
           (select count(*) from jsonb_array_elements(r.diff -> 'rows') x where x ->> 'table' not in ('citation', 'source', 'source_artifact', 'source_page')
              or (x ->> 'table' = 'citation' and (x ->> 'op' = 'drop' or x -> 'fields' -> 'locator' -> 1 ->> 'op' = 'cite')))::int as changes,
           coalesce((select sum(value::int) from jsonb_each_text(r.checks -> 'findings')), 0)::int as findings,
           case when (p.prescreen ->> 'revision')::int = r.n then p.prescreen ->> 'verdict' end as prescreen,
           case when (p.prereview ->> 'revision')::int = r.n then p.prereview ->> 'recommendation' end as recommendation,
           ${escalations(sql`p.id`)} as escalations
    from proposal p
    left join users a on a.id = p.author_user_id
    left join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
    where p.state in ('open', 'approved')
    order by p.tier nulls last, p.submitted_at nulls last, p.number
    limit 200
  `);
  return rows;
}

async function target(number: number) {
  const { rows } = await db.execute<{
    id: string;
    state: ProposalState;
    tier: number | null;
    n: number | null;
    escalations: Array<Escalation>;
  }>(sql`
    select p.id, p.state::text as state, p.tier, r.n, ${escalations(sql`p.id`)} as escalations
    from proposal p
    left join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
    where p.number = ${number}
  `);
  const found = rows[0];
  if (found == null) {
    error(404);
  }
  return found;
}

const activity = (
  proposalId: string,
  kind: string,
  actor: string,
  summary: string,
) => sql`
  insert into activity (actor_user_id, kind, proposal_id, changeset_id, entity_ids, polity_ids, summary)
  select ${actor}, ${kind}::activity_kind, p.id, p.head_changeset_id,
         coalesce((select array_agg(e::uuid) from proposal_revision r, jsonb_array_elements_text(r.diff -> 'entities') e
                   where r.proposal_id = p.id and r.changeset_id = p.head_changeset_id), '{}'),
         array_remove(array[p.polity_id], null), format(${summary}, p.number)
  from proposal p where p.id = ${proposalId}
`;

export async function decide({
  userId,
  number,
  verb,
  reason,
}: {
  userId: string;
  number: number;
  verb: "approve" | "reject" | "request_changes";
  reason: string;
}) {
  const [allowed, found] = await Promise.all([roles(userId), target(number)]);
  if (
    !(
      allowed.admin ||
      (allowed.reviewer && (verb === "request_changes" || found.tier === 1))
    )
  ) {
    error(403, "Only an admin can decide this proposal.");
  }
  if (
    !(
      found.state === "open" ||
      (verb === "reject" && found.state === "changes_requested")
    )
  ) {
    error(409, "This proposal is no longer open.");
  }
  if (verb !== "approve" && reason === "") {
    error(
      400,
      verb === "reject"
        ? "A rejection needs a reason."
        : "A request for changes needs a reason.",
    );
  }
  if (verb === "approve" && !allowed.admin && found.escalations.length > 0) {
    error(
      409,
      `This proposal needs an admin: ${found.escalations.join(", ")}.`,
    );
  }
  await db.transaction(async (tx) => {
    if (verb === "request_changes") {
      await tx.execute(sql`
        update proposal
        set state = 'changes_requested', decision_reason = ${reason}, decided_by = ${userId}, decided_at = now(), updated_at = now()
        where id = ${found.id}
      `);
      await tx.execute(
        activity(
          found.id,
          "changes_requested",
          userId,
          "Changes requested on proposal %s",
        ),
      );
      return;
    }
    await tx.execute(sql`
      update proposal
      set state = ${verb === "approve" ? "approved" : "open"}::proposal_state,
          decision_reason = ${reason === "" ? null : reason}, updated_at = now()
      where id = ${found.id}
    `);
    await tx.execute(sql`
      insert into proposal_job (proposal_id, kind, requested_by, output)
      values (${found.id}, ${verb === "approve" ? "publish" : "reject"}::proposal_job_kind, ${userId},
              ${JSON.stringify(reason === "" ? {} : { reason })}::jsonb)
    `);
    await tx.execute(notify);
  });
}

export async function prereview({
  userId,
  number,
  review,
}: {
  userId: string;
  number: number;
  review: Omit<Prereview, "revision" | "by" | "at">;
}) {
  const [allowed, found] = await Promise.all([roles(userId), target(number)]);
  if (!(allowed.admin || allowed.reviewer)) {
    error(403);
  }
  if (found.state !== "open" || found.n == null) {
    error(409, "Only an open, checked proposal takes a pre-review.");
  }
  const written: Prereview = {
    ...review,
    revision: found.n,
    by: userId,
    at: new Date().toISOString(),
  };
  await db.execute(sql`
    update proposal set prereview = ${JSON.stringify(written)}::jsonb, updated_at = now()
    where id = ${found.id}
  `);
  return written;
}

export async function revert({
  userId,
  number,
  reason,
}: {
  userId: string;
  number: number;
  reason: string;
}) {
  const [allowed, found] = await Promise.all([roles(userId), target(number)]);
  if (!(allowed.admin || (allowed.reviewer && found.tier === 1))) {
    error(403, "Only an admin can revert this proposal.");
  }
  if (found.state !== "published") {
    error(409, "Only a published proposal can be reverted.");
  }
  await db.transaction(async (tx) => {
    await tx.execute(sql`
      insert into proposal_job (proposal_id, kind, requested_by, output)
      values (${found.id}, 'revert', ${userId}, ${JSON.stringify({ reason })}::jsonb)
    `);
    await tx.execute(notify);
  });
}
