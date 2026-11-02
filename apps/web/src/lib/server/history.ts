import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { HistoryEntry } from "#lib/contrib.js";
import type { TalkEntity } from "#lib/server/talk.js";
import { db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";
import { publicPattern } from "#lib/server/talk.js";

const PAGE = 50;

const FACTS = ["result_value", "result_flow", "value_locator"];

const QUIET = new Set([
  "id",
  "k",
  "changeset_id",
  "created_at",
  "recorded",
  "recorded_from",
  "superseded_by",
  "valid_from_precision",
  "valid_to_precision",
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;

export const Before = v.pipe(v.string(), v.isoTimestamp());

const columns = sql`
  c.id::text as changeset, to_char(c.published_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') as at,
  c.author_kind::text as author_kind, c.pipeline, c.summary, ce.role::text as role,
  p.number, p.title, p.state::text as state,
  coalesce(f.display_name, f.handle) as author, f.handle, p.agent_name as agent
`;

async function record(publicId: string): Promise<TalkEntity> {
  const { rows } = await db.execute<
    Omit<TalkEntity, "name"> & { former: string | null }
  >(sql`
    select e.id, e.type::text as type, ea_public_id(e.type, e.id) as public_id,
           (select h.text from name_history h
            where h.entity_id = e.id and h.superseded_by in (
              select changeset_id from entity_merge where entity_id = e.id
              union all select changeset_id from entity_retirement where entity_id = e.id
            )
            order by h.is_preferred desc limit 1) as former
    from entity e
    where ${
      publicPattern.test(publicId)
        ? sql`e.id = ea_resolve(${publicId}) and ea_prefix(e.type) = split_part(${publicId}, '_', 1)`
        : sql`e.id = ${publicId}::uuid`
    }
  `);
  const found = rows[0];
  if (found == null) {
    error(404, "No record has that public ID.");
  }
  const { former, ...entity } = found;
  const { full } = await names([found.id]);
  return { ...entity, name: full.get(found.id) ?? former ?? found.public_id };
}

export async function history(publicId: string, before: string | null) {
  const entity = await record(publicId);
  const { rows } = await db.execute<HistoryEntry>(sql`
    select ${columns}
    from changeset_entity ce
    join changeset c on c.id = ce.changeset_id
    left join proposal p on p.head_changeset_id = c.id
    left join profile f on f.user_id = p.author_user_id
    where ce.entity_id = ${entity.id}::uuid
      and c.status = 'published'
      and (${before}::timestamptz is null or c.published_at < ${before}::timestamptz)
    order by c.published_at desc, c.id desc
    limit ${PAGE + 1}
  `);
  const page = rows.slice(0, PAGE);
  return {
    entity,
    entries: page,
    next: rows.length > PAGE ? (page.at(-1)?.at ?? null) : null,
  };
}

type Link = {
  table: string;
  column: string;
  history: boolean;
  keys: Array<string>;
};

let catalog: Promise<Array<Link>> | null = null;

const links = async () => {
  catalog ??= db
    .execute<Link>(
      sql`
        with core as (
          select c.oid, c.relname from pg_class c
          where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p') and not c.relispartition
            and c.relname <> all(${sql.param(FACTS)}::text[])
            and exists (select from pg_attribute s where s.attrelid = c.oid and s.attname = 'changeset_id'
                        and not s.attisdropped)
        ), types as (
          select 'entity' as name union all select unnest(enum_range(null::entity_type))::text
        ), linked as (
          select r.oid, r.relname, a.attname from core r
          join pg_constraint f on f.conrelid = r.oid and f.contype = 'f' and cardinality(f.conkey) = 1
          join pg_attribute a on a.attrelid = r.oid and a.attnum = f.conkey[1]
          where f.confrelid::regclass::text in (select name from types)
          union
          select r.oid, r.relname, 'id' from core r
          join pg_trigger t on t.tgrelid = r.oid and t.tgname = 'ea_entity'
        )
        select l.relname as table, l.attname as column,
               to_regclass(format('public.%I', l.relname || '_history')) is not null as history,
               coalesce((select array_agg(k.attname::text order by k.attnum) from pg_index i
                         join pg_attribute k on k.attrelid = i.indrelid and k.attnum = any(i.indkey)
                         where i.indrelid = l.oid and i.indisprimary), '{}') as keys
        from linked l where l.relname <> 'entity' order by 1, 2
      `,
    )
    .then(({ rows }) => {
      const linked = new Map<string, Array<string>>();
      for (const { table, column } of rows) {
        linked.set(table, [...(linked.get(table) ?? []), column]);
      }
      return rows.filter(({ table, column }) => {
        const found = linked.get(table) ?? [];
        const kept = found.includes("id")
          ? ["id"]
          : found.filter((name) => name !== "source_id");
        return (kept.length > 0 ? kept : found).includes(column);
      });
    })
    .catch((error_: unknown) => {
      catalog = null;
      throw error_;
    });
  return catalog;
};

export type FieldChange = {
  table: string;
  op: "add" | "change" | "drop";
  fields: Array<{ field: string; before: unknown; after: unknown }>;
};

const clipped = (value: unknown) => {
  if (value === "(,)") {
    return null;
  }
  const text =
    value == null || typeof value === "string" ? value : JSON.stringify(value);
  return typeof text === "string" && text.length > 400
    ? `${text.slice(0, 400)}…`
    : text;
};

export async function change(publicId: string, changeset: string) {
  const entity = await record(publicId);
  const { rows: found } = await db.execute<HistoryEntry>(sql`
    select ${columns}
    from changeset_entity ce
    join changeset c on c.id = ce.changeset_id
    left join proposal p on p.head_changeset_id = c.id
    left join profile f on f.user_id = p.author_user_id
    where ce.entity_id = ${entity.id}::uuid and ce.changeset_id = ${changeset}::uuid
      and c.status = 'published'
  `);
  const entry = found[0];
  if (entry == null) {
    error(404, "That changeset didn't change this record.");
  }
  const [catalogued, { rows: others }] = await Promise.all([
    links(),
    db.execute<{ id: string }>(sql`
      select changeset_id::text as id from changeset_entity
      where entity_id = ${entity.id}::uuid and changeset_id <> ${changeset}::uuid
    `),
  ]);
  const later = sql`${sql.param(others.map(({ id }) => id))}::uuid[]`;
  const keyed = new Map(catalogued.map(({ table, keys }) => [table, keys]));
  keyed.set("entity", ["id"]);
  const parts = [
    sql`
      select 'entity' as tbl, 'after' as side, to_jsonb(t) as row from entity t
      where t.id = ${entity.id}::uuid and t.changeset_id = ${changeset}::uuid
    `,
  ];
  for (const { table, column, history: kept } of catalogued) {
    const name = sql.raw(`public.${JSON.stringify(table)}`);
    const key = sql.raw(JSON.stringify(column));
    parts.push(sql`
      select ${table}, 'after', to_jsonb(t) from ${name} t
      where t.changeset_id = ${changeset}::uuid and t.${key} = ${entity.id}::uuid
    `);
    if (kept) {
      const twin = sql.raw(`public.${JSON.stringify(`${table}_history`)}`);
      parts.push(sql`
        select ${table}, 'before', to_jsonb(t) from ${twin} t
        where t.superseded_by = ${changeset}::uuid and t.${key} = ${entity.id}::uuid
      `);
      if (others.length > 0) {
        parts.push(sql`
          select ${table}, 'after', to_jsonb(t) from ${twin} t
          where t.superseded_by = any(${later}) and t.changeset_id = ${changeset}::uuid
            and t.${key} = ${entity.id}::uuid
        `);
      }
    }
  }
  const { rows } = await db.execute<{
    tbl: string;
    side: "before" | "after";
    row: Record<string, unknown>;
  }>(sql.join(parts, sql` union all `));
  const paired = new Map<
    string,
    {
      table: string;
      before: Record<string, unknown> | null;
      after: Record<string, unknown> | null;
    }
  >();
  for (const { tbl, side, row } of rows) {
    const keys = keyed.get(tbl) ?? [];
    const identity =
      keys.length > 0
        ? JSON.stringify(keys.map((key) => row[key] ?? null))
        : `${side}:${JSON.stringify(row)}`;
    const key = `${tbl}:${identity}`;
    const item = paired.get(key) ?? { table: tbl, before: null, after: null };
    item[side] = row;
    paired.set(key, item);
  }
  const changes: Array<FieldChange> = [];
  const referenced = new Set<string>();
  for (const { table, before, after } of paired.values()) {
    const fields = [
      ...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]),
    ]
      .filter((field) => !QUIET.has(field))
      .map((field) => ({
        field,
        before: clipped(before?.[field] ?? null),
        after: clipped(after?.[field] ?? null),
      }))
      .filter(
        (item) =>
          item.before !== entity.id &&
          item.after !== entity.id &&
          (item.before != null || item.after != null) &&
          (before == null || after == null || item.before !== item.after),
      );
    for (const { before: was, after: now } of fields) {
      for (const value of [was, now]) {
        if (typeof value === "string" && UUID.test(value)) {
          referenced.add(value);
        }
      }
    }
    if (fields.length > 0 || before == null || after == null) {
      changes.push({
        table,
        op: before == null ? "add" : after == null ? "drop" : "change",
        fields,
      });
    }
  }
  const { full } =
    referenced.size > 0
      ? await names([...referenced])
      : { full: new Map<string, string>() };
  changes.sort(
    (a, b) =>
      Number(b.table === "entity") - Number(a.table === "entity") ||
      a.table.localeCompare(b.table),
  );
  return { entity, entry, changes, names: Object.fromEntries(full) };
}
