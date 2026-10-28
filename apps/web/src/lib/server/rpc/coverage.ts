import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import {
  COUNTRY_TIERS,
  US_TIERS,
  type Column,
  type Goal,
} from "#lib/coverage.js";

const GOALS = [
  "county",
  "history",
  "turnout",
  "precinct",
  "precinct_shapes",
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
  major: v.number(),
  published: v.number(),
  lost: v.number(),
  upcoming: v.number(),
  pending: v.number(),
  populationTotal: v.number(),
  populationComplete: v.number(),
  populationAbsent: v.number(),
  populationEstimated: v.number(),
};
type Counts = Record<keyof typeof counts, number>;
const Code = v.pipe(v.string(), v.regex(/^[a-z]{2}$/));
const SUMS = sql.raw(
  Object.keys(counts)
    .map((key) => {
      const column = key.replaceAll(
        /[A-Z]/g,
        (letter) => `_${letter.toLowerCase()}`,
      );
      return `sum(g.${column}${key === "complete" ? " + g.upcoming" : ""})::${column.startsWith("population_") ? "float8" : "int"} as "${key}"`;
    })
    .join(", "),
);
const Ready = {
  total: v.number(),
  complete: v.number(),
  absent: v.number(),
  bare: v.number(),
  partial: v.number(),
  population: v.number(),
  populationReady: v.number(),
  populationAbsent: v.number(),
};
const Note = v.object({
  goal: v.string(),
  level: v.string(),
  from: v.nullable(v.number()),
  to: v.nullable(v.number()),
  note: v.string(),
  held: v.optional(v.number()),
  listed: v.optional(v.boolean()),
  lost: v.optional(v.string()),
  canvass: v.optional(v.string()),
  readiness: v.optional(v.string()),
  county: v.optional(v.nullable(v.string())),
  url: v.optional(v.string()),
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
      note: v.optional(v.nullable(v.string())),
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
const Cells = v.record(v.string(), v.nullable(v.object(counts)));
const ZERO = Object.fromEntries(
  Object.keys(counts).map((key) => [key, 0]),
) as Counts;

type Row = {
  goal: Goal;
  level: string;
  era: "before" | "mid" | "late";
  years: Array<number>;
} & Counts;
type Spec = {
  goal: Goal;
  era?: "before" | "since" | "mid" | "late";
  any?: boolean;
} | null;

function sum(rows: Array<Row>, spec: Spec, levels: ReadonlyArray<string>) {
  if (spec == null) {
    return null;
  }
  const total = { ...ZERO };
  for (const row of rows) {
    if (
      row.goal === spec.goal &&
      levels.includes(row.level) &&
      (spec.era == null ||
        (spec.era === "since" ? row.era !== "before" : row.era === spec.era))
    ) {
      for (const key of Object.keys(counts) as Array<keyof Counts>) {
        total[key] += row[key];
      }
    }
  }
  return spec.any === true
    ? {
        ...ZERO,
        total: total.total,
        complete:
          total.total - total.absent - total.bare - total.lost - total.pending,
        absent: total.absent,
        bare: total.bare,
        lost: total.lost,
        upcoming: total.upcoming,
        pending: total.pending,
      }
    : total;
}

function tiers(polity: string, rows: Array<Row>, levels: Polity["levels"]) {
  const isUs = polity === "us";
  const groups: Array<[string, ReadonlyArray<string>]> = isUs
    ? Object.entries(US_TIERS)
    : COUNTRY_TIERS.flatMap((tier) => {
        const own = levels.flatMap((level) =>
          level.goal === tier ? [level.key] : [],
        );
        return own.length === 0 ? [] : [[tier, own]];
      });
  return groups.map(([tier, keys]) => {
    const own = levels.filter((level) => keys.includes(level.key));
    const specs = (
      only: Array<Polity["levels"][number]>,
    ): Partial<Record<Column, Spec>> => {
      if (!isUs) {
        return {
          any: { goal: tier as Goal, any: true },
          constituency: { goal: tier as Goal },
          fine: only.some((level) => level.fine) ? { goal: "fine" } : null,
          shapes: only.some((level) => level.unit === "district")
            ? { goal: "shapes" }
            : null,
        };
      }
      if (tier === "local") {
        return { since_any: { goal: "local" } };
      }
      if (tier === "precinct_shapes") {
        return {
          precinct_2000: { goal: "precinct_shapes", era: "mid" },
          precinct_2016: { goal: "precinct_shapes", era: "late" },
        };
      }
      if (tier === "turnout") {
        return {
          before: { goal: "turnout", era: "before" },
          since: { goal: "turnout", era: "since" },
        };
      }
      return {
        before_any: { goal: "history", any: true },
        before_county: tier === "legislative" ? null : { goal: "history" },
        ...(tier === "legislative"
          ? { before_district: { goal: "history" } }
          : {}),
        before_shapes: { goal: "shapes_history" },
        since_any: { goal: "county", any: true },
        since_county: { goal: "county" },
        since_shapes: { goal: "shapes" },
        precinct_2000: { goal: "precinct", era: "mid" },
        precinct_2016: { goal: "precinct", era: "late" },
      };
    };
    const cells = (
      only: Array<Polity["levels"][number]>,
      found: ReadonlyArray<string>,
    ) =>
      Object.fromEntries(
        Object.entries(specs(only)).map(([column, spec]) => {
          const value = sum(rows, spec ?? null, found);
          return [column, isUs && value?.total === 0 ? null : value];
        }),
      );
    const main = new Set<Goal>(
      isUs
        ? ["history", "county", "local", "turnout", "precinct_shapes"]
        : [tier as Goal],
    );
    return {
      key: tier,
      cells: cells(own, keys),
      levels: keys.map((key) => ({
        key,
        years: [
          ...new Set(
            rows.flatMap((row) =>
              row.level === key && main.has(row.goal) && row.total > 0
                ? row.years
                : [],
            ),
          ),
        ].toSorted((a, b) => a - b),
        cells: cells(
          own.filter((level) => level.key === key),
          [key],
        ),
      })),
    };
  });
}

async function build() {
  const built = await db.execute<{
    builtAt: string | null;
    building: boolean;
    commit: string | null;
    stale: boolean;
  }>(sql`
    select built_at::text as "builtAt", built_at is null or started_at > built_at as building, commit,
      latest is not null and inputs is distinct from latest as stale
    from coverage_build
  `);
  return {
    builtAt: built.rows[0]?.builtAt ?? null,
    building: built.rows[0]?.building ?? false,
    commit: built.rows[0]?.commit ?? null,
    stale: built.rows[0]?.stale ?? false,
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
          "Coverage of every country the archive holds: its span, elections, contests, finest reporting unit, and how far each tier (national, regional, and local elections, referendums, and the European Parliament; for the US federal, statewide, state legislative, and local contests since 2000) is counted, as the share of races with results at each race's own constituency or district (US local governments: election years with any results); a race still to vote counts once it is in the archive with its candidates or options in full (upcoming), and once its date passes it needs results",
      }),
    )
    .output(
      v.object({
        builtAt: v.nullable(v.string()),
        building: v.boolean(),
        commit: v.nullable(v.string()),
        stale: v.boolean(),
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
            tiers: v.array(
              v.object({ key: v.string(), cell: v.nullable(v.object(counts)) }),
            ),
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
      const found = await db.execute<{ polity: string } & Row>(sql`
        select g.polity, g.goal, g.level, case when g.year < 2000 then 'before' when g.year < 2016 then 'mid' else 'late' end as era, array_agg(distinct g.year order by g.year) as years,
          ${SUMS}
        from coverage_goal g
        where g.goal in ('history', 'county', 'local', 'subnational', 'national', 'referendums', 'ep')
        group by 1, 2, 3, 4
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
          uncounted: polity.notes.filter(
            (note) =>
              note.lost == null &&
              note.canvass == null &&
              note.readiness == null,
          ).length,
          tiers: tiers(
            polity.polity,
            found.rows.filter((row) => row.polity === polity.polity),
            polity.levels,
          ).flatMap((tier) => {
            if (tier.key === "turnout" || tier.key === "precinct_shapes") {
              return [];
            }
            if (polity.polity !== "us" || tier.key === "local") {
              return [
                {
                  key: tier.key,
                  cell:
                    tier.cells[
                      polity.polity !== "us" ? "constituency" : "since_any"
                    ] ?? null,
                },
              ];
            }
            const parts = [tier.cells.before_any, tier.cells.since_any].filter(
              (part) => part != null,
            );
            const cell = { ...ZERO };
            for (const part of parts) {
              for (const key of Object.keys(counts) as Array<keyof Counts>) {
                cell[key] += part[key];
              }
            }
            return [{ key: tier.key, cell: parts.length === 0 ? null : cell }];
          }),
        })),
      };
    }),
  goals: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/{polity}",
        summary:
          "One country's coverage by tier (polity is a lowercase ISO code, or eu): national, regional, and local elections, referendums, and the European Parliament (for the US: federal, statewide, state legislative, local, and turnout), each with counts at every grain (any results, by constituency or county, finer, and map shapes; for the US split before 2000 and from 2000, with precinct results split into 2000–2015 and 2016 on; precinct shapes are their own tier, counting each precinct once per state and year, deduplicated across sources, with the target from 2016) for the tier and for each kind of election in it, plus the cited sources of the denominators and what is left uncounted; complete includes upcoming, the races still to vote that the archive holds with their candidates or options in full, and pending counts those still short of them",
      }),
    )
    .input(v.object({ polity: Code }))
    .output(
      v.object({
        builtAt: v.nullable(v.string()),
        building: v.boolean(),
        commit: v.nullable(v.string()),
        stale: v.boolean(),
        summary: Summary,
        tiers: v.array(
          v.object({
            key: v.string(),
            cells: Cells,
            levels: v.array(
              v.object({
                key: v.string(),
                years: v.array(v.number()),
                cells: Cells,
              }),
            ),
          }),
        ),
        readiness: v.array(
          v.object({
            level: v.string(),
            year: v.number(),
            ...Ready,
            states: v.array(v.object({ state: v.string(), ...Ready })),
          }),
        ),
        unshaped: v.array(
          v.object({
            tier: v.string(),
            goal: v.string(),
            from: v.number(),
            to: v.number(),
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
      const found = await db.execute<Row>(sql`
        select g.goal, g.level, case when g.year < 2000 then 'before' when g.year < 2016 then 'mid' else 'late' end as era, array_agg(distinct g.year order by g.year) as years, ${SUMS}
        from coverage_goal g
        where g.polity = ${input.polity}
        group by 1, 2, 3
      `);
      const ready = await db.execute<
        { level: string; year: number; state: string } & Record<
          keyof typeof Ready,
          number
        >
      >(sql`
        select g.level, g.year, g.state, g.total, g.complete, g.absent, g.bare, g.partial,
          coalesce(g.population_total, 0)::float8 as population, coalesce(g.population_complete, 0)::float8 as "populationReady",
          coalesce(g.population_absent, 0)::float8 as "populationAbsent"
        from coverage_goal g
        where g.polity = ${input.polity} and g.goal = 'readiness'
        order by g.level, g.state
      `);
      const readiness: Array<
        { level: string; year: number; states: typeof ready.rows } & Record<
          keyof typeof Ready,
          number
        >
      > = [];
      for (const row of ready.rows) {
        let group = readiness.find((found) => found.level === row.level);
        if (group == null) {
          group = {
            level: row.level,
            year: row.year,
            total: 0,
            complete: 0,
            absent: 0,
            bare: 0,
            partial: 0,
            population: 0,
            populationReady: 0,
            populationAbsent: 0,
            states: [],
          };
          readiness.push(group);
        }
        for (const key of Object.keys(Ready) as Array<keyof typeof Ready>) {
          group[key] += row[key];
        }
        group.states.push(row);
      }
      const years = await db.execute<{
        goal: string;
        level: string;
        year: number;
        unshaped: number;
      }>(sql`
        select g.goal, g.level, g.year, sum(g.unshaped)::int as unshaped
        from coverage_goal g
        where g.polity = ${input.polity} and g.goal in ('shapes', 'shapes_history') and g.unshaped > 0
        group by 1, 2, 3
      `);
      const unshaped = Object.entries(US_TIERS).flatMap(([tier, levels]) =>
        ["shapes", "shapes_history"].flatMap((goal) => {
          const byYear = new Map<number, number>();
          for (const row of years.rows) {
            if (
              row.goal === goal &&
              (levels as ReadonlyArray<string>).includes(row.level)
            ) {
              byYear.set(row.year, (byYear.get(row.year) ?? 0) + row.unshaped);
            }
          }
          const sorted = [...byYear.entries()].toSorted((a, b) => a[0] - b[0]);
          let total = 0;
          for (const [, count] of sorted) {
            total += count;
          }
          let best: { from: number; to: number } | null = null;
          for (const [start, [from]] of sorted.entries()) {
            let sum = 0;
            for (const [to, count] of sorted.slice(start)) {
              sum += count;
              if (sum * 2 > total) {
                if (best == null || to - from < best.to - best.from) {
                  best = { from, to };
                }
                break;
              }
            }
          }
          return best == null ? [] : [{ tier, goal, ...best }];
        }),
      );
      return {
        ...status,
        summary,
        tiers: tiers(input.polity, found.rows, summary.levels),
        readiness,
        unshaped,
      };
    }),
  gaps: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/{polity}/gaps",
        summary:
          "Where one country's coverage goal falls short, optionally within one tier: by state or region, then by level within it, then by year within a level, largest gap first",
      }),
    )
    .input(
      v.object({
        polity: Code,
        goal: v.picklist(GOALS),
        tier: v.optional(
          v.picklist([...Object.keys(US_TIERS), ...COUNTRY_TIERS]),
        ),
        state: v.optional(v.pipe(v.string(), v.maxLength(8))),
        level: v.optional(v.pipe(v.string(), v.maxLength(40))),
      }),
    )
    .output(
      v.array(
        v.object({
          key: v.string(),
          label: v.nullable(v.string()),
          path: v.nullable(v.string()),
          ...counts,
        }),
      ),
    )
    .handler(async ({ input }) => {
      const state = input.state ?? null;
      const level = state == null ? null : (input.level ?? null);
      const key = sql.raw(
        state == null ? "g.state" : level == null ? "g.level" : "g.year::text",
      );
      const tier = input.tier ?? null;
      const usLevels =
        input.polity === "us" && tier != null && tier in US_TIERS
          ? US_TIERS[tier as keyof typeof US_TIERS].join(",")
          : null;
      const found = await db.execute<
        { key: string; label: string | null; path: string | null } & Counts
      >(sql`
        select r.*,
          case when ${state}::text is not null then null
            when ${input.polity} = 'us' then (select n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
              where p.iso_code = 'US-' || r.key order by n.lang <> 'en', n.recorded_from desc, n.id limit 1)
            else (select c.regions ->> r.key from coverage_polity c where c.polity = ${input.polity}) end as label,
          case when ${state}::text is null then (select s.path from polity p join slug s on s.entity_id = p.id and s.is_current
            where p.iso_code = upper(${input.polity}) || '-' || r.key limit 1) end as path
        from (
          select ${key} as key, ${SUMS} from coverage_goal g
          where g.polity = ${input.polity} and g.goal = ${input.goal}
            and (${state}::text is null or g.state = ${state})
            and (${level}::text is null or g.level = ${level})
            and (${input.goal} <> 'precinct_shapes' or g.year >= 2016)
            and (${tier}::text is null or g.level = any(coalesce(string_to_array(${usLevels}::text, ','),
              (select array_agg(l ->> 'key') from coverage_polity c cross join jsonb_array_elements(c.levels) l
                where c.polity = ${input.polity} and l ->> 'goal' = ${tier}))))
            group by 1
        ) r
        order by case when ${input.goal} = 'local' then r."populationTotal" - r."populationComplete"
          else r.total - r.complete end desc, r.key desc
      `);
      return found.rows;
    }),
};
