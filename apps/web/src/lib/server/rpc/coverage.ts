import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";

const GOALS = [
  "county",
  "history",
  "turnout",
  "precinct",
  "shapes",
  "shapes_history",
  "local",
  "national",
  "fine",
  "subnational",
  "referendums",
  "ep",
] as const;
const counts = {
  total: v.number(),
  complete: v.number(),
  absent: v.number(),
  bare: v.number(),
  totals: v.number(),
  partial: v.number(),
  coarse: v.number(),
  turnout: v.number(),
  precincts: v.number(),
  shaped: v.number(),
  unshaped: v.number(),
  units: v.number(),
  undrawn: v.number(),
  icpsr: v.number(),
  populationTotal: v.number(),
  populationComplete: v.number(),
  populationAbsent: v.number(),
  populationEstimated: v.number(),
};
type Counts = Record<keyof typeof counts, number>;
const Current = v.optional(
  v.pipe(
    v.union([v.boolean(), v.string()]),
    v.transform((value) => [true, "1", "true"].includes(value)),
  ),
  false,
);
const Code = v.pipe(v.string(), v.regex(/^[a-z]{2}$/));
const SUMS = sql.raw(
  Object.keys(counts)
    .map((key) => {
      const column = key.replaceAll(
        /[A-Z]/g,
        (letter) => `_${letter.toLowerCase()}`,
      );
      return `sum(g.${column})::${column.startsWith("population_") ? "float8" : "int"} as "${key}"`;
    })
    .join(", "),
);
const Note = v.object({
  goal: v.string(),
  level: v.string(),
  from: v.nullable(v.number()),
  to: v.nullable(v.number()),
  note: v.string(),
  held: v.optional(v.number()),
  listed: v.optional(v.boolean()),
});
const Summary = v.object({
  polity: v.string(),
  name: v.string(),
  first: v.nullable(v.number()),
  last: v.nullable(v.number()),
  elections: v.number(),
  contests: v.number(),
  finest: v.nullable(v.string()),
  finestNoun: v.nullable(v.string()),
  regions: v.record(v.string(), v.string()),
  levels: v.array(
    v.object({
      key: v.string(),
      label: v.optional(v.string()),
      goal: v.string(),
      fine: v.boolean(),
      unit: v.string(),
    }),
  ),
  notes: v.array(Note),
  sources: v.record(
    v.string(),
    v.object({
      title: v.string(),
      publisher: v.optional(v.string()),
      url: v.string(),
      note: v.optional(v.nullable(v.string())),
    }),
  ),
});
type Polity = v.InferOutput<typeof Summary>;

async function build() {
  const built = await db.execute<{
    builtAt: string | null;
    building: boolean;
  }>(sql`
    select built_at::text as "builtAt", built_at is null or started_at > built_at as building from coverage_build
  `);
  return {
    builtAt: built.rows[0]?.builtAt ?? null,
    building: built.rows[0]?.building ?? false,
  };
}

async function summaries(polity: string | null) {
  const found = await db.execute<Polity>(sql`
    select c.polity, coalesce((select n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
        where p.iso_code = upper(c.polity) order by n.lang <> 'en', n.recorded_from desc, n.id limit 1), upper(c.polity)) as name,
      c.first, c.last, c.elections, c.contests, c.finest, c.finest_noun as "finestNoun", c.regions, c.levels, c.notes,
      c.sources
    from coverage_polity c
    where ${polity}::text is null or c.polity = ${polity}
    order by c.polity = 'us' desc, c.contests desc
  `);
  return found.rows;
}

export const coverage = {
  overview: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage",
        summary:
          "Coverage of every country the archive holds: its span, elections, contests, finest reporting unit, and how far it has reached its current goal and its map shapes; the current year, still under way, counts only with current=1",
      }),
    )
    .input(v.optional(v.object({ current: Current }), {}))
    .output(
      v.object({
        builtAt: v.nullable(v.string()),
        building: v.boolean(),
        totals: v.object({
          elections: v.number(),
          contests: v.number(),
          first: v.nullable(v.number()),
          last: v.nullable(v.number()),
        }),
        polities: v.array(
          v.object({
            ...v.omit(Summary, ["regions", "levels", "notes", "sources"])
              .entries,
            uncounted: v.number(),
            goals: v.array(v.object({ goal: v.picklist(GOALS), ...counts })),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const [status, polities, totals] = await Promise.all([
        build(),
        summaries(null),
        db.execute<{
          elections: number;
          contests: number;
          first: number | null;
          last: number | null;
        }>(sql`
          select count(distinct e.id)::int as elections, count(c.id)::int as contests,
            min(extract(year from e.date_from))::int as first, max(extract(year from e.date_from))::int as last
          from election e join contest c on c.election_id = e.id
          where e.date_from >= '1700-01-01' and e.date_from <= current_date and e.status <> 'scheduled' and not c.notional
        `),
      ]);
      const found = await db.execute<
        { polity: string; goal: (typeof GOALS)[number] } & Counts
      >(sql`
        select g.polity, g.goal, ${SUMS} from coverage_goal g
        where g.goal in ('county', 'national', 'shapes')
          and (${input.current} or g.year <> extract(year from current_date))
        group by 1, 2
      `);
      return {
        ...status,
        totals: totals.rows[0] ?? {
          elections: 0,
          contests: 0,
          first: null,
          last: null,
        },
        polities: polities.map((polity) => ({
          polity: polity.polity,
          name: polity.name,
          first: polity.first,
          last: polity.last,
          elections: polity.elections,
          contests: polity.contests,
          finest: polity.finest,
          finestNoun: polity.finestNoun,
          uncounted: polity.notes.length,
          goals: found.rows.filter((row) => row.polity === polity.polity),
        })),
      };
    }),
  goals: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/{polity}",
        summary:
          "One country's coverage goals (polity is a lowercase ISO code, or eu), each with the contests or constituencies it counts, how many meet it, and why the rest fall short, plus the country's levels, the cited sources of its denominators, and what is left uncounted; the current year counts only with current=1",
      }),
    )
    .input(v.object({ polity: Code, current: Current }))
    .output(
      v.object({
        builtAt: v.nullable(v.string()),
        building: v.boolean(),
        summary: Summary,
        goals: v.array(v.object({ goal: v.picklist(GOALS), ...counts })),
        counted: v.array(
          v.object({ level: v.string(), years: v.array(v.number()) }),
        ),
        parts: v.array(
          v.object({
            goal: v.picklist(["history", "turnout"]),
            part: v.picklist(["national", "legislature", "recent", "earlier"]),
            total: v.number(),
            complete: v.number(),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const [status, [summary]] = await Promise.all([
        build(),
        summaries(input.polity),
      ]);
      if (summary == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const found = await db.execute<
        { goal: (typeof GOALS)[number] } & Counts
      >(sql`
        select g.goal, ${SUMS} from coverage_goal g
        where g.polity = ${input.polity} and (${input.current} or g.year <> extract(year from current_date))
        group by g.goal
      `);
      const parts = await db.execute<{
        goal: "history" | "turnout";
        part: "national" | "legislature" | "recent" | "earlier";
        total: number;
        complete: number;
      }>(sql`
        select g.goal,
          case when g.goal = 'turnout' then case when g.year >= 2000 then 'recent' else 'earlier' end
            when g.level in ('state_upper', 'state_lower') then 'legislature' else 'national' end as part,
          sum(g.total)::int as total, sum(g.complete)::int as complete
        from coverage_goal g
        where g.polity = 'us' and ${input.polity} = 'us' and g.goal in ('history', 'turnout')
          and (${input.current} or g.year <> extract(year from current_date))
        group by 1, 2 order by 1, 2 desc
      `);
      const counted = await db.execute<{
        level: string;
        years: Array<number>;
      }>(sql`
        select g.level, array_agg(distinct g.year order by g.year) as years
        from coverage_goal g
        where g.polity = ${input.polity} and ${input.polity} <> 'us' and g.goal not in ('fine', 'shapes')
          and (${input.current} or g.year <> extract(year from current_date))
        group by g.level
      `);
      return {
        ...status,
        summary,
        counted: counted.rows,
        parts: parts.rows,
        goals: GOALS.flatMap((goal) =>
          found.rows.filter((row) => row.goal === goal),
        ),
      };
    }),
  gaps: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/{polity}/gaps",
        summary:
          "Where one country's coverage goal falls short: by state or region, then by level within it, then by year within a level, largest gap first; the current year counts only with current=1",
      }),
    )
    .input(
      v.object({
        polity: Code,
        goal: v.picklist(GOALS),
        state: v.optional(v.pipe(v.string(), v.maxLength(8))),
        level: v.optional(v.pipe(v.string(), v.maxLength(40))),
        current: Current,
      }),
    )
    .output(
      v.array(
        v.object({ key: v.string(), label: v.nullable(v.string()), ...counts }),
      ),
    )
    .handler(async ({ input }) => {
      const state = input.state ?? null;
      const level = state == null ? null : (input.level ?? null);
      const key = sql.raw(
        state == null ? "g.state" : level == null ? "g.level" : "g.year::text",
      );
      const found = await db.execute<
        { key: string; label: string | null } & Counts
      >(sql`
        select r.*,
          case when ${state}::text is not null then null
            when ${input.polity} = 'us' then (select n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
              where p.iso_code = 'US-' || r.key order by n.lang <> 'en', n.recorded_from desc, n.id limit 1)
            else (select c.regions ->> r.key from coverage_polity c where c.polity = ${input.polity}) end as label
        from (
          select ${key} as key, ${SUMS} from coverage_goal g
          where g.polity = ${input.polity} and g.goal = ${input.goal}
            and (${state}::text is null or g.state = ${state})
            and (${level}::text is null or g.level = ${level})
            and (${input.current} or g.year <> extract(year from current_date))
          group by 1
        ) r
        order by case when ${input.goal} = 'local' then r."populationTotal" - r."populationComplete"
          else r.total - r.complete end desc, r.key desc
      `);
      return found.rows;
    }),
};
