import { call, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Checks, Diff, NameKind, Scheme } from "#lib/contrib.js";
import type { Sheet, Upload } from "#lib/sheet.js";
import type { Lookup } from "#lib/workspace.js";
import {
  hex,
  languages,
  nameKinds,
  schemes,
  sourceKinds,
  stages,
} from "#lib/contrib.js";
import { db } from "#lib/server/db/index.js";
import { names } from "#lib/server/rpc/archive.js";
import { router } from "#lib/server/rpc/router.js";
import { outcomeKinds } from "#lib/sheet.js";
import {
  contestKinds,
  electionKinds,
  isRef,
  optionKinds,
  questionTypes,
  relationKinds,
} from "#lib/workspace.js";

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

const Id = v.pipe(v.string(), v.uuid());
const Fraction = v.pipe(v.number(), v.minValue(0), v.maxValue(1));
const Region = v.pipe(
  v.tuple([Fraction, Fraction, Fraction, Fraction]),
  v.check(([x0, y0, x1, y1]) => x0 < x1 && y0 < y1),
);
const Page = v.pipe(v.number(), v.integer(), v.minValue(1));
const Key = Raw(60);

export const Workspace = v.variant("template", [
  v.object({
    template: v.literal("election"),
    election: v.object({
      polity: Raw(2048),
      parent: Raw(2048),
      kind: Raw(20),
      date: Raw(10),
      name: Raw(300),
      lang: Raw(35),
    }),
    contests: v.pipe(
      v.array(
        v.object({
          name: Raw(300),
          kind: Raw(20),
          question: Raw(30),
          area: Raw(2048),
          office: Raw(2048),
          body: Raw(2048),
          magnitude: Raw(4),
          options: v.pipe(
            v.array(
              v.object({
                kind: Raw(20),
                person: Raw(2048),
                label: Raw(300),
                party: Raw(2048),
              }),
            ),
            v.maxLength(500),
          ),
        }),
      ),
      v.maxLength(200),
    ),
    evidence: Evidence,
  }),
  v.object({
    template: v.literal("results"),
    contests: v.pipe(
      v.array(
        v.object({
          record: Raw(2048),
          stage: Raw(20),
          columns: v.pipe(v.array(Key), v.maxLength(200)),
          rows: v.pipe(
            v.array(
              v.object({
                unit: v.union([
                  Id,
                  v.object({ scheme: Raw(40), code: Raw(100) }),
                ]),
                name: Raw(300),
                values: v.record(Key, Raw(40)),
                marks: v.record(Key, v.object({ page: Page, region: Region })),
              }),
            ),
            v.maxLength(2000),
          ),
          printed: v.record(Key, Raw(40)),
          outcomes: v.record(Key, Raw(20)),
        }),
      ),
      v.maxLength(20),
    ),
    evidence: v.nullish(
      v.object({
        url: Raw(2048),
        upload: v.nullable(Id),
        title: Raw(500),
        publisher: Raw(200),
        kind: v.picklist(sourceKinds),
      }),
      null,
    ),
  }),
  v.object({
    template: v.literal("merge"),
    pairs: v.pipe(
      v.array(v.object({ record: Raw(2048), into: Raw(2048) })),
      v.maxLength(500),
    ),
    evidence: Evidence,
  }),
  v.object({
    template: v.literal("lineage"),
    party: Raw(2048),
    rows: v.pipe(
      v.array(
        v.object({
          party: Raw(2048),
          kind: Raw(30),
          other: Raw(2048),
          date: Raw(10),
          election: Raw(2048),
        }),
      ),
      v.maxLength(200),
    ),
    drops: v.pipe(v.array(Id), v.maxLength(200)),
    evidence: Evidence,
  }),
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
          op: v.literal("results"),
          contest_id: Id,
          stage: v.picklist(stages),
          rows: v.pipe(
            v.array(
              v.object({
                unit: v.union([
                  Id,
                  v.object({ scheme: Line(40), code: Line(100) }),
                ]),
                option: v.nullable(Id),
                measure: v.pipe(v.string(), v.regex(/^[a-z_]{1,40}$/u)),
                channel: v.literal("all"),
                count_round: v.literal(0),
                value: v.pipe(v.number(), v.finite(), v.minValue(0)),
                page: v.optional(Page),
                region: v.optional(Region),
              }),
            ),
            v.minLength(1),
            v.maxLength(50_000),
          ),
          outcomes: v.optional(
            v.pipe(
              v.array(v.object({ option: Id, kind: v.picklist(outcomeKinds) })),
              v.maxLength(200),
            ),
          ),
          evidence: Cited,
        }),
        v.object({
          op: v.literal("election"),
          election: v.object({
            polity: Id,
            parent: v.optional(Id),
            kind: v.picklist(electionKinds),
            status: v.picklist(["held", "scheduled"]),
            date: v.pipe(v.string(), v.isoDate()),
            name: Line(300),
            lang: v.picklist(languages),
            contests: v.pipe(
              v.array(
                v.object({
                  name: Line(300),
                  kind: v.picklist(contestKinds),
                  question: v.optional(v.picklist(questionTypes)),
                  area: v.optional(Id),
                  office: v.optional(Id),
                  body: v.optional(Id),
                  magnitude: v.optional(
                    v.pipe(v.number(), v.integer(), v.minValue(1)),
                  ),
                  options: v.pipe(
                    v.array(
                      v.object({
                        kind: v.picklist(Object.values(optionKinds).flat()),
                        person: v.optional(Id),
                        person_name: v.optional(Line(300)),
                        label: v.optional(Line(300)),
                        party: v.optional(Id),
                      }),
                    ),
                    v.maxLength(500),
                  ),
                }),
              ),
              v.minLength(1),
              v.maxLength(200),
            ),
          }),
          evidence: v.tuple([v.literal("e1")]),
        }),
        v.object({ op: v.literal("merge"), id: Id, into: Id, evidence: Cited }),
        v.object({
          op: v.literal("lineage"),
          party: Id,
          kind: v.picklist(relationKinds),
          other: v.optional(Id),
          date: v.optional(
            v.pipe(v.string(), v.regex(/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/u)),
          ),
          election_id: v.optional(Id),
          evidence: v.tuple([v.literal("e1")]),
        }),
        v.object({
          op: v.literal("drop"),
          table: v.literal("party_relation"),
          id: Id,
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
      e1: v.union([
        v.object({
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
        v.object({
          upload: Id,
          title: Line(500),
          publisher: Line(200),
          kind: v.picklist(sourceKinds),
        }),
      ]),
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

export type Relation = {
  id: string;
  kind: (typeof relationKinds)[number];
  from: string;
  from_name: string | null;
  to: string | null;
  to_name: string | null;
  date: string | null;
  precision: "day" | "month" | "year" | "unknown";
  election_name: string | null;
};

export async function relations(party: string) {
  const { rows } = await db.execute<Relation>(sql`
    select r.id, r.kind::text as kind, r.from_party_id as "from", r.to_party_id as "to",
           r.date::text as date, r.date_precision::text as precision,
           (select n.text from name n where n.entity_id = r.from_party_id
            order by n.is_preferred desc, n.id limit 1) as from_name,
           (select n.text from name n where n.entity_id = r.to_party_id
            order by n.is_preferred desc, n.id limit 1) as to_name,
           (select n.text from name n where n.entity_id = r.election_id
            order by n.is_preferred desc, n.id limit 1) as election_name
    from party_relation r
    where r.from_party_id = ${party} or r.to_party_id = ${party}
    order by r.date nulls last, r.kind, r.id
    limit 200
  `);
  return rows;
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
    from: string | null;
    to: string | null;
    polity: string | null;
  }>(sql`
    select e.id, e.type::text as type,
           coalesce(pe.birth_date, el.date_from, ce.date_from)::text as "from",
           coalesce(pe.death_date, el.date_to, ce.date_to)::text as "to",
           (select n.text from name n where n.entity_id = coalesce(el.polity_id, pa.polity_id, ce.polity_id)
            order by n.is_preferred desc, n.id limit 1) as polity,
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
    left join person pe on pe.id = e.id
    left join election el on el.id = e.id
    left join party pa on pa.id = e.id
    left join contest co on co.id = e.id
    left join election ce on ce.id = co.election_id
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
              from: entity.from,
              to: entity.to,
              polity: entity.polity,
            };
  }
  return result;
}

export async function sheet(id: string): Promise<Sheet> {
  const [options, sets] = await Promise.all([
    db.execute<Sheet["options"][number]>(sql`
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
      where o.contest_id = ${id}
      order by o.ballot_position nulls last, o.k
    `),
    db.execute<{
      k: number;
      id: string;
      stage: string;
      source: string | null;
    }>(sql`
      select r.k, r.id, r.stage::text as stage, s.title as source
      from result_set r left join source s on s.id = r.source_id
      where r.contest_id = ${id} and r.status = 'active'
      order by r.is_default desc, r.stage, r.k
      limit 1
    `),
  ]);
  const set = sets.rows[0];
  if (set == null) {
    return { id, options: options.rows, places: [], measures: [], set: null };
  }
  const [places, measures] = await Promise.all([
    db.execute<Sheet["places"][number]>(sql`
      select u.id as unit, u.scheme_code as scheme, u.code,
             coalesce((select n.text from name n where n.entity_id = u.id
                       order by n.is_preferred desc, n.id limit 1),
                      (select n.text from name n where n.entity_id = u.area_id
                       order by n.is_preferred desc, n.id limit 1), u.code) as name
      from reporting_unit u
      where u.k in (select distinct v.unit_k from result_value v where v.result_set_k = ${set.k})
      order by u.code, u.id
      limit 2000
    `),
    db.execute<{ code: string }>(sql`
      select distinct m.code
      from result_value v join measure m on m.id = v.measure
      where v.result_set_k = ${set.k} and v.option_k = 0
    `),
  ]);
  return {
    id,
    options: options.rows,
    places: places.rows,
    measures: measures.rows.map((row) => row.code),
    set: { id: set.id, stage: set.stage, source: set.source },
  };
}

export async function upload(id: string, userId: string) {
  const { rows } = await db.execute<Upload>(sql`
    select id, filename, pages from upload
    where id = ${id} and owner_user_id = ${userId} and status = 'ready'
  `);
  return rows[0] ?? null;
}

export function refsOf(workspace: v.InferOutput<typeof Workspace>) {
  switch (workspace.template) {
    case "election": {
      return [
        workspace.election.polity,
        workspace.election.parent,
        ...workspace.contests.flatMap((contest) => [
          contest.area,
          contest.office,
          contest.body,
          ...contest.options.flatMap((option) => [
            isRef(option.person) ? option.person : "",
            option.party,
          ]),
        ]),
      ].filter((ref) => ref.trim() !== "");
    }
    case "results": {
      return workspace.contests.map((contest) => contest.record);
    }
    case "merge": {
      return workspace.pairs.flatMap((pair) => [pair.record, pair.into]);
    }
    case "lineage": {
      return [
        workspace.party,
        ...workspace.rows.flatMap((row) => [
          row.party,
          row.other,
          row.election,
        ]),
      ].filter((ref) => ref.trim() !== "");
    }
    default: {
      return workspace.rows.map((row) => row.record);
    }
  }
}
