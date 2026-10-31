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
import {
  assessed,
  ballotValues,
  labelKinds,
  labelTypes,
  schemes,
} from "#lib/contrib.js";
import { authDb, db } from "#lib/server/db/index.js";

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
  const { rows } = await authDb.execute<{ role: "admin" | "reviewer" }>(sql`
    select role from user_role where user_id = ${userId}
  `);
  return {
    admin: rows.some((row) => row.role === "admin"),
    reviewer: rows.some((row) => row.role === "reviewer"),
  };
}

export async function editable(entityId: string): Promise<Editable> {
  const [
    names,
    notes,
    ids,
    colors,
    kind,
    images,
    sources,
    citations,
    open,
    options,
    sets,
    labels,
  ] = await Promise.all([
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
    db.execute<{
      party: boolean;
      person: boolean;
      contest: boolean;
      type: string | null;
      listed: boolean;
    }>(sql`
      select (select type::text from entity where id = ${entityId}) as type,
             exists (select from contest_option where contest_id = ${entityId}
                     and kind in ('candidate', 'ticket', 'list')) as listed,
             exists (select from party where id = ${entityId}) as party,
             exists (select from person where id = ${entityId}) as person,
             exists (select from contest where id = ${entityId}) as contest
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
    db.execute<NonNullable<Editable["results"]>["options"][number]>(sql`
      select o.id, o.kind::text as kind, o.ballot_position as position,
             coalesce(
               o.ballot_label,
               (select string_agg(coalesce(c.ballot_name, (
                  select n.text from name n where n.entity_id = c.person_id
                  order by n.is_preferred desc, n.id limit 1
                )), ' / ') from candidacy c where c.option_id = o.id),
               (select n.text from name n where n.entity_id = o.party_id
                order by n.is_preferred desc, n.id limit 1),
               o.kind::text
             ) as label
      from contest_option o
      where o.contest_id = ${entityId}
      order by o.ballot_position nulls last, o.k
    `),
    db.execute<NonNullable<Editable["results"]>["sets"][number]>(sql`
      select r.id, r.stage::text as stage, r.status::text as status, r.is_default,
             c.pipeline, s.title as source, r.as_of::text,
             (select count(*)::int from (
                select from result_value v where v.result_set_k = r.k limit 100000
              ) x) as cells
      from result_set r
      join changeset c on c.id = r.changeset_id
      left join source s on s.id = r.source_id
      where r.contest_id = ${entityId} and r.status = 'active'
      order by r.is_default desc, r.stage, r.k
    `),
    db.execute<Editable["labels"]["held"][number]>(sql`
      select l.id, l.kind::text as kind, l.value, l.assessor, l.valid_from::text, l.valid_to::text,
             s.title as source
      from label l
      left join source s on s.id = l.source_id
      where l.entity_id = ${entityId}
      order by l.kind, l.valid_from nulls first, l.id
    `),
  ]);
  const type = kind.rows[0]?.type ?? null;
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
    labels: {
      held: labels.rows,
      kinds: labelKinds.filter(
        (label) =>
          (labelTypes[label] == null ||
            (type != null && labelTypes[label].includes(type))) &&
          !(label === "ballot" && kind.rows[0]?.listed === true),
      ),
    },
    results:
      kind.rows[0]?.contest === true
        ? { options: options.rows, sets: sets.rows }
        : null,
  };
}

export async function cells({
  id,
  offset,
  limit,
  unit,
}: {
  id: string;
  offset: number;
  limit: number;
  unit?: string;
}) {
  const { rows } = await db.execute<{
    k: number;
    contest: string;
    stage: string;
    is_default: boolean;
    pipeline: string;
    total: number;
  }>(sql`
    select r.k, r.contest_id as contest, r.stage::text as stage, r.is_default, c.pipeline,
           (select count(*)::int from result_value v where v.result_set_k = r.k) as total
    from result_set r
    join changeset c on c.id = r.changeset_id
    where r.id = ${id}
  `);
  const set = rows[0];
  if (set == null) {
    error(404);
  }
  const found = await db.execute<{
    unit: string;
    scheme: string;
    code: string;
    name: string;
    option: string | null;
    measure: string;
    channel: string;
    count_round: number;
    value: number;
    page: number | null;
    region: Array<number> | null;
  }>(sql`
    select u.id as unit, u.scheme_code as scheme, u.code,
           coalesce((select n.text from name n where n.entity_id = u.id
                     order by n.is_preferred desc, n.id limit 1), u.code) as name,
           o.id as option, m.code as measure, ch.code as channel, v.count_round, v.value::float8 as value,
           p.page_number as page,
           case when l.x0 is not null then array[l.x0, l.y0, l.x1, l.y1]::float8[] end as region
    from result_value v
    join reporting_unit u on u.k = v.unit_k
    left join contest_option o on o.k = v.option_k and v.option_k <> 0
    join measure m on m.id = v.measure
    join channel ch on ch.id = v.channel
    left join value_locator l on l.result_set_k = v.result_set_k and l.unit_k = v.unit_k
      and l.option_k = v.option_k and l.channel = v.channel and l.measure = v.measure
      and l.count_round = v.count_round
    left join source_page p on p.id = l.page_id
    where v.result_set_k = ${set.k} and (${unit ?? null}::uuid is null or u.id = ${unit ?? null}::uuid)
    order by u.code, u.id, o.ballot_position nulls first, o.k nulls first, v.measure, v.channel, v.count_round
    offset ${offset} limit ${limit}
  `);
  return {
    result_set: id,
    contest: set.contest,
    stage: set.stage,
    is_default: set.is_default,
    pipeline: set.pipeline,
    total: set.total,
    offset,
    cells: found.rows,
  };
}

const ballot = new Set<string>(ballotValues);

const fitLabels = (
  labeled: Array<Extract<Change, { kind: "label" }>>,
  type: string | null,
) => {
  for (const change of labeled) {
    const types = labelTypes[change.label];
    if (
      (types != null && (type == null || !types.includes(type))) ||
      (change.assessor == null && assessed.has(change.label)) ||
      (change.label === "ballot" && !ballot.has(change.value))
    ) {
      error(400, "That kind of label doesn't fit this record.");
    }
  }
};

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
    change.kind === "results" ||
    change.kind === "correct" ||
    change.kind === "name" ||
    change.kind === "cite" ||
    change.kind === "label" ||
    change.kind.startsWith("color_") ||
    (change.kind === "translate" && change.origin === "official");
  const canCite = (change: Change) =>
    change.kind === "image"
      ? hasDates(change)
      : change.kind !== "translate" || change.origin === "official";
  if (evidence == null && changes.some((change) => requiresSource(change))) {
    error(
      400,
      "A name, color, logo date, citation, label, official translation, or result needs a source.",
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
  const labeled = changes.flatMap((change) =>
    change.kind === "label" ? [change] : [],
  );
  const unlabeled = changes.flatMap((change) =>
    change.kind === "label_drop" ? [change.id] : [],
  );
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
  const hasResults = changes.some((change) => change.kind === "results");
  const corrected = changes.flatMap((change) =>
    change.kind === "correct" ? [change.resultSet] : [],
  );
  const sourceId = evidence?.type === "source" ? evidence.sourceId : null;
  const uploadId = evidence?.type === "upload" ? evidence.uploadId : null;
  const { rows } = await db.execute<{
    ok: boolean;
    party: boolean;
    held: boolean;
    type: string | null;
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
       and (${!hasResults} or exists (select from contest where id = ${entityId}))
       and (select count(*) from result_set
            where id = any(${`{${corrected.join(",")}}`}::uuid[]) and contest_id = ${entityId} and status = 'active')
             = ${new Set(corrected).size}
       and (${image == null} or exists (select from person where id = ${entityId})
            or exists (select from party where id = ${entityId}))
       and (select count(*) from citation c join changeset ch on ch.id = c.changeset_id
            where c.id = any(${`{${uncited.join(",")}}`}::uuid[]) and c.entity_id = ${entityId}
              and ch.pipeline = 'contrib' and c.locator ->> 'op' = 'cite')
             = ${new Set(uncited).size}
       and (select count(*) from label where id = any(${`{${unlabeled.join(",")}}`}::uuid[]) and entity_id = ${entityId})
             = ${new Set(unlabeled).size} as ok,
       (select type::text from entity where id = ${entityId} and retired_by is null) as type,
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
  fitLabels(labeled, rows[0].type);
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
      case "results": {
        return {
          op: "results",
          contest_id: entityId,
          stage: change.stage,
          rows: change.rows,
        };
      }
      case "correct": {
        return {
          op: "correct",
          result_set: change.resultSet,
          cells: change.cells,
        };
      }
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
      case "label": {
        return {
          op: "label",
          values: {
            entity_id: entityId,
            kind: change.label,
            value: change.value,
            ...(change.assessor != null && { assessor: change.assessor }),
            ...(change.from != null && { valid_from: change.from }),
            ...(change.to != null && { valid_to: change.to }),
          },
        };
      }
      case "label_drop": {
        return { op: "drop", table: "label", id: change.id };
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
      pages: Record<
        string,
        { upload: string; page: number; width: number; height: number }
      > | null;
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
              ) o) as originals,
             (select json_object_agg(l.id, json_build_object(
                       'upload', up.upload_id, 'page', up.page_number, 'width', up.width, 'height', up.height
                     ))
              from (
                select distinct x -> 'locator' ->> 'page_id' as id
                from jsonb_array_elements(r.diff -> 'rows') x
                where x ->> 'table' = 'result_value' and x ? 'locator'
              ) l
              cross join lateral (
                select u.upload_id, u.page_number, u.width, u.height
                from upload_page u
                where u.image_key = coalesce(
                  (select y -> 'fields' -> 'image_key' ->> 1 from jsonb_array_elements(r.diff -> 'rows') y
                   where y ->> 'table' = 'source_page' and y -> 'key' ->> 'id' = l.id limit 1),
                  (select sp.image_key from source_page sp where sp.id::text = l.id)
                )
                limit 1
              ) up) as pages
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
    fold: "awaiting" | "mismatch" | null;
  }>(sql`
    select p.number, p.title, p.state::text as state, p.tier, a.name as author,
           p.submitted_at::text,
           case when p.state = 'approved' and p.decided_by is not null and r.diff ? 'fold'
                then case when r.diff -> 'fold' ? 'mismatch' then 'mismatch' else 'awaiting' end end as fold,
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
    order by (p.state = 'approved' and r.diff ? 'fold'), p.tier nulls last, p.submitted_at nulls last, p.number
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
