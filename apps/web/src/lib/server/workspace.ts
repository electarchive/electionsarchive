import { call, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Checks, Diff, NameKind, Scheme } from "#lib/contrib.js";
import type { Lookup } from "#lib/workspace.js";
import {
  hex,
  languages,
  nameKinds,
  schemes,
  sourceKinds,
} from "#lib/contrib.js";
import { db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";
import { router } from "#lib/server/rpc/router.js";

const Line = (max: number) =>
  v.pipe(
    v.string(),
    v.normalize("NFC"),
    v.trim(),
    v.minLength(1),
    v.maxLength(max),
  );

const Raw = (max: number) => v.pipe(v.string(), v.maxLength(max));

const Rows = <T extends v.ObjectEntries>(cells: T) =>
  v.pipe(v.array(v.object({ record: Raw(2048), ...cells })), v.maxLength(500));

const Evidence = v.nullish(
  v.object({
    url: Raw(2048),
    title: Raw(500),
    publisher: Raw(200),
    kind: v.picklist(sourceKinds),
  }),
  null,
);

export const Workspace = v.variant("template", [
  v.object({
    template: v.literal("identifiers"),
    rows: Rows({ scheme: Raw(20), value: Raw(100) }),
    evidence: Evidence,
  }),
  v.object({
    template: v.literal("names"),
    rows: Rows({ text: Raw(500), lang: Raw(20), kind: Raw(20) }),
    evidence: Evidence,
  }),
  v.object({
    template: v.literal("colors"),
    rows: Rows({ color: Raw(20), from: Raw(20), to: Raw(20) }),
    evidence: Evidence,
  }),
]);

const Id = v.pipe(v.string(), v.uuid());
const Cited = v.optional(v.tuple([v.literal("e1")]));
const Day = v.optional(v.pipe(v.string(), v.isoDate()));

export const Draft = v.object({
  summary: v.pipe(v.string(), v.normalize("NFC"), v.trim(), v.maxLength(300)),
  ops: v.pipe(
    v.array(
      v.union([
        v.object({
          op: v.literal("add"),
          table: v.literal("external_id"),
          values: v.object({
            entity_id: Id,
            scheme: v.picklist(schemes),
            value: Raw(100),
          }),
          evidence: Cited,
        }),
        v.object({
          op: v.literal("add"),
          table: v.literal("name"),
          values: v.object({
            entity_id: Id,
            text: Line(500),
            lang: v.picklist(languages),
            kind: v.picklist(nameKinds),
          }),
          evidence: Cited,
        }),
        v.object({
          op: v.literal("add"),
          table: v.literal("party_color"),
          values: v.object({
            party_id: Id,
            color: v.pipe(v.string(), v.regex(hex)),
            valid_from: Day,
            valid_to: Day,
          }),
          evidence: Cited,
        }),
      ]),
    ),
    v.maxLength(500),
  ),
  evidence: v.optional(
    v.object({
      e1: v.object({
        url: v.pipe(
          v.string(),
          v.trim(),
          v.maxLength(2048),
          v.url(),
          v.regex(/^https?:\/\//iu),
        ),
        title: Line(500),
        publisher: Line(200),
        kind: v.picklist(sourceKinds),
      }),
    }),
  ),
  workspace: Workspace,
});

export async function latest(number: number, kind: "check" | "submit") {
  const { rows } = await db.execute<{
    status: string;
    error: string | null;
    tier: number | null;
    checks: Checks | null;
    diff: Diff | null;
    finished_at: string | null;
  }>(sql`
    select j.status::text, j.output ->> 'error' as error, (j.output ->> 'tier')::int as tier,
           j.output -> 'checks' as checks, j.output -> 'diff' as diff, j.finished_at::text
    from proposal_job j join proposal p on p.id = j.proposal_id
    where p.number = ${number} and j.kind = ${kind}::proposal_job_kind
    order by j.created_at desc
    limit 1
  `);
  return rows[0] ?? null;
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;

const pathOf = (ref: string) => {
  let text = ref.trim();
  if (/^https?:\/\//iu.test(text)) {
    try {
      text = new URL(text).pathname;
    } catch {
      return null;
    }
  }
  try {
    text = decodeURIComponent(text);
  } catch {
    return null;
  }
  text = (text.split(/[?#]/u, 1)[0] ?? "").replaceAll(/^\/+|\/+$/gu, "");
  return text.startsWith("id/") ? text.slice(3) : text;
};

async function located(path: string) {
  try {
    if (path.startsWith("people/")) {
      const person = await call(router.people.locate, { slug: path.slice(7) });
      return person.id;
    }
    if (path.startsWith("parties/")) {
      const segments = path.slice(8).split("/");
      const party = await call(router.parties.locate, {
        polity:
          segments.length > 1 ? segments.slice(0, -1).join("/") : undefined,
        slug: segments.at(-1) ?? "",
      });
      return party.id;
    }
  } catch (error_) {
    if (error_ instanceof ORPCError) {
      return null;
    }
    throw error_;
  }
  const { rows } = await db.execute<{ id: string }>(sql`
    select entity_id as id from slug where path = ${path}
  `);
  return rows[0]?.id ?? null;
}

export async function records(refs: Array<string>) {
  const unique = [...new Set(refs.map((ref) => ref.trim()))].filter(
    (ref) => ref !== "",
  );
  const ids = new Map<string, string | null>();
  await Promise.all(
    unique.map(async (ref) => {
      const path = pathOf(ref);
      ids.set(
        ref,
        path == null || path === ""
          ? null
          : uuid.test(path)
            ? path.toLowerCase()
            : await located(path),
      );
    }),
  );
  const wanted = new Set(ids.values().filter((id) => id != null))
    .values()
    .toArray();
  const { rows } = await db.execute<{
    id: string;
    type: string;
    gone: boolean;
    ids: Array<{ scheme: Scheme; value: string }>;
    names: Array<{ text: string; lang: string; kind: NameKind }>;
    colors: Array<{
      color: string;
      valid_from: string | null;
      valid_to: string | null;
    }>;
  }>(sql`
    select e.id, e.type::text as type,
           exists (select from entity_merge m where m.entity_id = e.id)
             or exists (select from entity_retirement r where r.entity_id = e.id) as gone,
           coalesce((
             select json_agg(json_build_object('scheme', x.scheme, 'value', x.value) order by x.scheme, x.value)
             from external_id x
             where x.entity_id = e.id and x.scheme = any(${`{${schemes.join(",")}}`}::text[])
           ), '[]') as ids,
           coalesce((
             select json_agg(json_build_object('text', n.text, 'lang', n.lang, 'kind', n.kind) order by n.text)
             from name n where n.entity_id = e.id
           ), '[]') as names,
           coalesce((
             select json_agg(json_build_object('color', c.color, 'valid_from', c.valid_from, 'valid_to', c.valid_to)
                             order by c.valid_from nulls first)
             from party_color c where c.party_id = e.id
           ), '[]') as colors
    from entity e
    where e.id = any(${sql.param(wanted)}::uuid[])
  `);
  const { full } = await names(rows.map((row) => row.id));
  const entities = new Map(rows.map((row) => [row.id, row]));
  const result: Record<string, Lookup> = {};
  for (const ref of unique) {
    const id = ids.get(ref);
    const entity = id == null ? null : entities.get(id);
    result[ref] =
      entity == null
        ? { state: "missing" }
        : entity.gone
          ? { state: "retired" }
          : {
              state: "found",
              id: entity.id,
              type: entity.type,
              name: full.get(entity.id) ?? entity.id,
              ids: entity.ids,
              names: entity.names,
              colors: entity.colors,
            };
  }
  return result;
}
