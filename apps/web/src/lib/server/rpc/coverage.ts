import { openapi } from "@orpc/openapi";
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
};
const Current = v.optional(
  v.pipe(
    v.union([v.boolean(), v.string()]),
    v.transform((value) => [true, "1", "true"].includes(value)),
  ),
  false,
);
const SUMS = sql.raw(
  Object.keys(counts)
    .map((key) => `sum(g.${key})::int as ${key}`)
    .join(", "),
);

export const coverage = {
  goals: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage",
        summary:
          "US coverage goals, each with the contests it counts, how many meet it, and why the rest fall short; the current year, still under way, counts only with current=1",
      }),
    )
    .input(v.optional(v.object({ current: Current }), {}))
    .output(
      v.object({
        builtAt: v.nullable(v.string()),
        building: v.boolean(),
        goals: v.array(v.object({ goal: v.picklist(GOALS), ...counts })),
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
      const built = await db.execute<{
        builtAt: string | null;
        building: boolean;
      }>(sql`
        select built_at::text as "builtAt", built_at is null or started_at > built_at as building from coverage_build
      `);
      const found = await db.execute<
        { goal: (typeof GOALS)[number] } & Record<keyof typeof counts, number>
      >(sql`
        select g.goal, ${SUMS} from coverage_goal g
        where g.polity = 'us' and (${input.current} or g.year <> extract(year from current_date))
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
        where g.polity = 'us' and g.goal in ('history', 'turnout')
          and (${input.current} or g.year <> extract(year from current_date))
        group by 1, 2 order by 1, 2 desc
      `);
      return {
        parts: parts.rows,
        builtAt: built.rows[0]?.builtAt ?? null,
        building: built.rows[0]?.building ?? false,
        goals: GOALS.flatMap((goal) =>
          found.rows.filter((row) => row.goal === goal),
        ),
      };
    }),
  gaps: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/gaps",
        summary:
          "Where a US coverage goal falls short: by state, then by office within a state, then by year within an office, largest gap first; the current year counts only with current=1",
      }),
    )
    .input(
      v.object({
        goal: v.picklist(GOALS),
        state: v.optional(v.pipe(v.string(), v.regex(/^[A-Z]{2}$/))),
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
        { key: string; label: string | null } & Record<
          keyof typeof counts,
          number
        >
      >(sql`
        select r.*,
          (select n.text from name n where n.entity_id = p.id and n.is_preferred order by n.lang <> 'en', n.recorded_from desc, n.id limit 1) as label
        from (
          select ${key} as key, ${SUMS} from coverage_goal g
          where g.polity = 'us' and g.goal = ${input.goal} and (${state}::text is null or g.state = ${state})
            and (${level}::text is null or g.level = ${level})
            and (${input.current} or g.year <> extract(year from current_date))
          group by 1
        ) r
        left join polity p on ${state == null} and p.iso_code = 'US-' || r.key
        order by r.total - r.complete desc, r.key desc
      `);
      return found.rows;
    }),
};
