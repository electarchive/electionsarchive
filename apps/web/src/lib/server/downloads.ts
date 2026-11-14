import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { describe, fileUrl, latestUrl, tag } from "#lib/downloads.js";
import { m } from "#lib/paraglide/messages.js";
import { remember } from "#lib/server/cache.js";
import { twinResponse } from "#lib/server/contrib-twins.js";
import { db } from "#lib/server/db/index.js";
import { releaseMarkdown } from "#lib/server/downloads-markdown.js";

const UTC = `'YYYY-MM-DD"T"HH24:MI:SS"Z"'`;
const PAGE = 50;
const NEUTRAL = [false, undefined, true] as const;

const Year = v.pipe(
  v.nullish(v.union([v.number(), v.string()])),
  v.transform((value) => {
    if (value == null) {
      return null;
    }
    const year = typeof value === "number" ? value : Number(value.slice(0, 4));
    return Number.isFinite(year) ? year : null;
  }),
);
const Count = v.optional(v.number(), 0);
const list = <T extends v.GenericSchema>(item: T) =>
  v.optional(v.array(item), []);

const Spanned = { first: Year, last: Year };
const Kind = v.object({ kind: v.string(), count: Count, ...Spanned });
const Question = v.object({ type: v.string(), count: Count, first: Year });
const Formula = v.object({ formula: v.string(), contests: Count });
const Coded = v.object({ code: v.string(), rows: Count });

const CountrySchema = v.object({
  code: v.string(),
  name: v.string(),
  ...Spanned,
  elections: Count,
  contests: Count,
  result_sets: Count,
  results: Count,
  levels: list(v.object({ kind: v.string(), contests: Count, ...Spanned })),
  units: list(
    v.object({
      scheme: v.string(),
      noun: v.nullish(v.string(), null),
      results: Count,
      result_sets: Count,
      ...Spanned,
    }),
  ),
  election_kinds: list(Kind),
  ballot_questions: list(Question),
  formulas: list(Formula),
});

const SummarySchema = v.object({
  totals: v.optional(v.record(v.string(), v.number()), {}),
  span: v.optional(
    v.object({
      first: v.nullish(v.string(), null),
      last: v.nullish(v.string(), null),
    }),
    { first: null, last: null },
  ),
  countries: list(CountrySchema),
  election_kinds: list(Kind),
  contest_kinds: list(v.object({ kind: v.string(), count: Count })),
  ballot_questions: list(Question),
  formulas: list(Formula),
  measures: list(Coded),
  channels: list(Coded),
  stages: list(v.object({ stage: v.string(), count: Count })),
  shapes: list(
    v.object({
      scheme: v.string(),
      noun: v.nullish(v.string(), null),
      country: v.nullish(v.string(), null),
      shapes: Count,
      areas: Count,
      ...Spanned,
    }),
  ),
  tables: list(
    v.object({ table: v.string(), rows: Count, bytes: Count, files: Count }),
  ),
});

const FileSchema = v.object({
  file: v.string(),
  bundle: v.string(),
  table: v.string(),
  part: v.optional(v.string(), ""),
  rows: Count,
  bytes: Count,
  sha256: v.string(),
});

const ManifestSchema = v.object({
  watermark_published_at: v.nullish(v.string(), null),
  bundles: v.optional(
    v.record(
      v.string(),
      v.object({
        license: v.string(),
        license_url: v.string(),
        scope: v.string(),
      }),
    ),
    {},
  ),
  held: list(v.string()),
  files: list(FileSchema),
  summary: v.optional(v.unknown()),
});

export type Summary = v.InferOutput<typeof SummarySchema>;
export type Country = v.InferOutput<typeof CountrySchema>;
export type ReleaseFile = v.InferOutput<typeof FileSchema>;

export const SHARED = [
  "electionsarchive.sql",
  "manifest.json",
  "ATTRIBUTION.md",
  "SHA256SUMS",
  "gaps.csv",
] as const;

const summaryOf = (value: unknown) => {
  if (value == null) {
    return null;
  }
  const parsed = v.safeParse(SummarySchema, value);
  return parsed.success ? parsed.output : null;
};

const TotalsSchema = v.nullish(v.record(v.string(), v.number()), null);

const bounds = sql`
  with r as (
    select calver, created_at, doi,
      coalesce((manifest ->> 'watermark_published_at')::timestamptz, created_at) as upto
    from release
  ), w as (
    select r.*, lag(upto) over (order by upto, created_at) as since from r
  )
`;

export async function releases() {
  return remember(
    "downloads:v1:releases",
    async () => {
      const found = await db.execute<{
        calver: string;
        created_at: string;
        doi: string | null;
        upto: string;
        since: string | null;
        files: number;
        bytes: number;
        totals: unknown;
        changes: number;
      }>(sql`
        ${bounds}
        select w.calver, to_char(w.created_at at time zone 'UTC', ${sql.raw(UTC)}) as created_at, w.doi,
          to_char(w.upto at time zone 'UTC', ${sql.raw(UTC)}) as upto,
          to_char(w.since at time zone 'UTC', ${sql.raw(UTC)}) as since,
          coalesce(jsonb_array_length(m.manifest -> 'files'), 0)::int as files,
          coalesce((select sum((f ->> 'bytes')::bigint) from jsonb_array_elements(m.manifest -> 'files') f), 0)::float8 as bytes,
          m.manifest -> 'summary' -> 'totals' as totals,
          (select count(*)::int from changeset c
            where c.status = 'published' and c.published_at <= w.upto
              and (w.since is null or c.published_at > w.since)) as changes
        from w join release m on m.calver = w.calver
        order by w.upto desc, w.created_at desc
      `);
      return found.rows.map((row) => {
        const totals = v.safeParse(TotalsSchema, row.totals);
        return {
          calver: row.calver,
          created_at: row.created_at,
          doi: row.doi,
          upto: row.upto,
          since: row.since,
          files: row.files,
          bytes: row.bytes,
          changes: row.changes,
          totals: totals.success ? totals.output : null,
        };
      });
    },
    ...NEUTRAL,
  );
}

export type ReleaseRow = Awaited<ReturnType<typeof releases>>[number];

async function manifest(calver: string) {
  return remember(
    `downloads:v1:manifest:${calver}`,
    async () => {
      const found = await db.execute<{ manifest: unknown }>(
        sql`select manifest from release where calver = ${calver}`,
      );
      const parsed = v.safeParse(ManifestSchema, found.rows[0]?.manifest ?? {});
      if (!parsed.success) {
        return null;
      }
      const { summary, ...rest } = parsed.output;
      return { ...rest, summary: summaryOf(summary) };
    },
    ...NEUTRAL,
  );
}

export async function changeSummary(calver: string) {
  return remember(
    `downloads:v1:changes:${calver}`,
    async () => {
      const found = await db.execute<{
        country: string | null;
        pipeline: string | null;
        kind: string;
        count: number;
      }>(sql`
        ${bounds}
        select k.country, c.pipeline, c.kind::text as kind, count(*)::int as count
        from w
        join changeset c on c.status = 'published' and c.published_at <= w.upto
          and (w.since is null or c.published_at > w.since)
        cross join lateral (
          select coalesce(
            nullif(upper(split_part(c.scope ->> 'polity', '-', 1)), ''),
            case when split_part(c.pipeline, '.', 1) ~ '^[a-z]{2}$' then upper(split_part(c.pipeline, '.', 1)) end
          ) as country
        ) k
        where w.calver = ${calver}
        group by 1, 2, 3
      `);
      return found.rows;
    },
    ...NEUTRAL,
  );
}

export type ChangeGroup = Awaited<ReturnType<typeof changeSummary>>[number];

export async function changes(
  calver: string,
  {
    offset = 0,
    pipeline = null,
    country = null,
    limit = PAGE,
  }: {
    offset?: number;
    pipeline?: string | null;
    country?: string | null;
    limit?: number | null;
  },
) {
  const found = await db.execute<{
    id: string;
    pipeline: string | null;
    kind: string;
    summary: string;
    published_at: string;
    path: string | null;
  }>(sql`
    ${bounds}
    select c.id::text as id, c.pipeline, c.kind::text as kind, c.summary,
      to_char(c.published_at at time zone 'UTC', ${sql.raw(UTC)}) as published_at,
      (select s.path from polity p join slug s on s.entity_id = p.id and s.is_current
        where p.iso_code = c.scope ->> 'polity' limit 1) as path
    from w
    join changeset c on c.status = 'published' and c.published_at <= w.upto
      and (w.since is null or c.published_at > w.since)
    where w.calver = ${calver}
      and (${pipeline}::text is null or c.pipeline = ${pipeline}::text)
      and (${country}::text is null or coalesce(
          nullif(upper(split_part(c.scope ->> 'polity', '-', 1)), ''),
          case when split_part(c.pipeline, '.', 1) ~ '^[a-z]{2}$' then upper(split_part(c.pipeline, '.', 1)) end,
          '-'
        ) = ${country}::text)
    order by c.published_at desc, c.id desc
    offset ${offset} limit ${limit == null ? sql.raw("all") : sql`${limit + 1}`}
  `);
  if (limit == null) {
    return { changes: found.rows, next: null };
  }
  return {
    changes: found.rows.slice(0, limit),
    next: found.rows.length > limit ? offset + limit : null,
  };
}

export type Change = Awaited<ReturnType<typeof changes>>["changes"][number];

export async function release(calver: string) {
  const all = await releases();
  const index = all.findIndex((row) => row.calver === calver);
  const row = all[index];
  if (row == null) {
    error(404);
  }
  const previous = all[index + 1] ?? null;
  const [found, groups, first] = await Promise.all([
    manifest(calver),
    changeSummary(calver),
    changes(calver, {}),
  ]);
  return {
    ...row,
    isLatest: index === 0,
    latest: all[0]?.calver ?? calver,
    previous:
      previous == null
        ? null
        : { calver: previous.calver, totals: previous.totals },
    bundles: found?.bundles ?? {},
    held: found?.held ?? [],
    files: found?.files ?? [],
    summary: found?.summary ?? null,
    groups,
    page: first,
  };
}

export type Release = Awaited<ReturnType<typeof release>>;

export async function latestCalver() {
  return (await releases())[0]?.calver ?? null;
}

export async function hasFile(calver: string, file: string) {
  if (new Set<string>(SHARED).has(file)) {
    return (await releases()).some((row) => row.calver === calver);
  }
  const found = await manifest(calver);
  return found?.files.some((entry) => entry.file === file) ?? false;
}

export async function downloadsTwin(
  format: "atom" | "md" | "json",
  origin: string,
  calver: string | null,
) {
  const [all, chosen] = await Promise.all([
    releases(),
    calver == null
      ? latestCalver().then(async (found) =>
          found == null ? null : release(found),
        )
      : release(calver),
  ]);
  const full =
    chosen == null ? null : await changes(chosen.calver, { limit: null });
  return twinResponse({
    format,
    origin,
    page: calver == null ? "/downloads" : `/downloads/${calver}`,
    title:
      calver == null
        ? m.downloads_title()
        : m.downloads_release_title({ version: calver }),
    subtitle: chosen == null ? m.downloads_lede() : describe(chosen, "en"),
    markdown: () =>
      releaseMarkdown({
        origin,
        all,
        release: chosen,
        changes: full?.changes ?? [],
        isIndex: calver == null,
      }),
    json: () => ({
      version: chosen?.calver ?? null,
      latest: all[0]?.calver ?? null,
      release:
        chosen == null
          ? null
          : {
              version: chosen.calver,
              tag: `data-${chosen.calver}`,
              github: tag(chosen.calver),
              doi: chosen.doi,
              published_at: chosen.created_at,
              data_as_of: chosen.upto,
              changes_since: chosen.since,
              bytes: chosen.bytes,
              bundles: chosen.bundles,
              held: chosen.held,
              summary: chosen.summary,
              shared: SHARED.map((file) => ({
                file,
                url: fileUrl(origin, chosen.calver, file),
                latest: chosen.isLatest ? latestUrl(origin, file) : null,
              })),
              files: chosen.files.map((file) => ({
                ...file,
                url: fileUrl(origin, chosen.calver, file.file),
                latest: chosen.isLatest ? latestUrl(origin, file.file) : null,
              })),
              change_summary: chosen.groups,
              changes: (full?.changes ?? []).map((change) => ({
                ...change,
                path: change.path == null ? null : `${origin}/${change.path}`,
              })),
            },
      releases: all.map((row) => ({
        version: row.calver,
        url: `${origin}/downloads/${row.calver}`,
        published_at: row.created_at,
        data_as_of: row.upto,
        files: row.files,
        bytes: row.bytes,
        changes: row.changes,
        totals: row.totals,
      })),
    }),
  });
}
