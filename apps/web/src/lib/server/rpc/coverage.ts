import { dev } from "$app/env";
import { openapi } from "@orpc/openapi";
import { os as base, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Column, Goal } from "#lib/coverage.js";
import { COUNTRY_TIERS, US_TIERS } from "#lib/coverage.js";
import { os as cached } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { Polity as PolityPath } from "#lib/server/territory.js";

const os = dev ? base : cached;

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
  knownGap: v.number(),
  blocked: v.number(),
  inapplicable: v.number(),
  upcoming: v.number(),
  pending: v.number(),
  populationTotal: v.number(),
  populationComplete: v.number(),
  populationAbsent: v.number(),
  populationEstimated: v.number(),
};
type Counts = Record<keyof typeof counts, number>;
const Code = v.pipeAsync(PolityPath, v.regex(/^[a-z]{2}$/));
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
  knownGap: v.number(),
  blocked: v.number(),
  inapplicable: v.number(),
  population: v.number(),
  populationReady: v.number(),
  populationAbsent: v.number(),
};
const CATEGORIES = ["known_gap", "blocked", "not_applicable"] as const;
const ACCESS = ["open", "browser", "blocked", "manual", "none"] as const;
const Note = v.object({
  goal: v.string(),
  level: v.string(),
  from: v.nullable(v.number()),
  to: v.nullable(v.number()),
  note: v.string(),
  held: v.optional(v.number()),
  listed: v.optional(v.boolean()),
  category: v.optional(v.picklist(CATEGORIES)),
  state: v.optional(v.nullable(v.string())),
  stage: v.optional(v.nullable(v.string())),
  label: v.optional(v.nullable(v.string())),
  checked: v.optional(v.nullable(v.string())),
  count: v.optional(v.number()),
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
          total.total -
          total.absent -
          total.bare -
          total.knownGap -
          total.blocked -
          total.pending,
        absent: total.absent,
        bare: total.bare,
        knownGap: total.knownGap,
        blocked: total.blocked,
        inapplicable: total.inapplicable,
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
        ...(tier === "legislative" && { before_district: { goal: "history" } }),
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
      c.first, c.last, c.elections, c.contests, c.finest, c.finest_noun as "finestNoun", c.regions, c.levels,
      (select coalesce(jsonb_agg(n - 'contests' - 'elections' order by i), '[]') from jsonb_array_elements(c.notes) with ordinality as t (n, i)) as notes,
      c.sources
    from coverage_polity c
    where ${polity}::text is null or c.polity = ${polity}
    order by c.polity = 'us' desc, c.contests desc
  `);
  return found.rows;
}

const STATUSES = ["held", "queued", "blocked", "out_of_scope", "open"] as const;
const CensusClass = v.object({
  key: v.string(),
  name: v.string(),
  tier: v.string(),
  tierName: v.string(),
  category: v.string(),
  goal: v.string(),
  from: v.nullable(v.number()),
  to: v.nullable(v.number()),
  status: v.picklist(STATUSES),
  reason: v.nullable(v.string()),
  share: v.nullable(v.number()),
  next: v.nullable(v.string()),
});
type ClassRow = v.InferOutput<typeof CensusClass>;

async function censusClasses(polity: string | null) {
  const found = await db.execute<
    Omit<ClassRow, "status"> & {
      polity: string;
      disposition: "blocked" | "out_of_scope" | null;
      held: boolean;
      queued: boolean;
    }
  >(sql`
    with recursive depth as (
      select t.id, 0 as depth from census_tier t where t.parent_tier_id is null
      union all
      select t.id, d.depth + 1 from census_tier t join depth d on t.parent_tier_id = d.id
    )
    select lower(p.iso_code) as polity, k.key, coalesce(k.name_en, k.name) as name, t.key as tier,
      coalesce(t.name_en, t.name) as "tierName", k.category::text as category, k.goal,
      extract(year from k.valid_from)::int as "from", extract(year from k.valid_to)::int as "to",
      k.disposition::text as disposition, k.disposition_reason as reason,
      exists (
        select 1 from contest_class cc join contest c on c.id = cc.contest_id join election e on e.id = c.election_id
        where cc.class_id = k.id and not c.notional and e.status <> 'scheduled' and e.date_from <= current_date
      ) as held,
      (select sum(g.complete)::float8 / nullif(sum(g.total), 0) from coverage_goal g
        where g.polity = lower(p.iso_code) and g.goal = k.goal and g.level = k.key) as share,
      least(
        (select min(d.date) from census_date d where d.class_id = k.id and d.date > current_date),
        (select min(e.date_from) from contest_class cc join contest c on c.id = cc.contest_id join election e on e.id = c.election_id
          where cc.class_id = k.id and e.date_from > current_date)
      )::text as next,
      exists (
        select 1 from coverage_polity cp cross join jsonb_array_elements(cp.classes) x
        where cp.polity = lower(p.iso_code) and x ->> 'key' = k.key and x ->> 'status' = 'queued'
      ) as queued
    from census_class k
    join census_tier t on t.id = k.tier_id
    join depth d on d.id = t.id
    join polity p on p.id = k.polity_id
    where ${polity}::text is null or p.iso_code = upper(${polity})
    order by polity, d.depth, t.key, k.valid_to is not null, k.key
  `);
  return found.rows.map(
    ({ polity, disposition, held, queued, ...row }): [string, ClassRow] => [
      polity,
      {
        ...row,
        status: held ? "held" : (disposition ?? (queued ? "queued" : "open")),
        reason: held ? null : row.reason,
      },
    ],
  );
}

function tightestHalf(sorted: Array<[number, number]>) {
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
  return best;
}

export const coverage = {
  overview: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage",
        summary:
          "Coverage of every country the archive holds: its span, elections, contests, finest reporting unit, and how far each tier (national, regional, and local elections, referendums, and the European Parliament; for the US federal, statewide, state legislative, and local contests since 2000) is counted, as the share of races with results at each race's own constituency or district (US local governments: election years with any results); a race still to vote counts once it is in the archive with its candidates or options in full (upcoming), and once its date passes it needs results; each country also lists its census classes of election (by tier, with status: held, queued, blocked, out of scope, or open; the reason for blocked and out-of-scope classes; the share counted; and the next scheduled date)",
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
            classes: v.array(CensusClass),
            tiers: v.array(
              v.object({ key: v.string(), cell: v.nullable(v.object(counts)) }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const [status, polities, totals, classes] = await Promise.all([
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
        censusClasses(null),
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
          classes: classes.flatMap(([key, row]) =>
            key === polity.polity ? [row] : [],
          ),
          uncounted: polity.notes.filter(
            (note) =>
              note.category == null &&
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
          "One country's coverage by tier (polity is a lowercase ISO code, or eu): national, regional, and local elections, referendums, and the European Parliament (for the US: federal, statewide, state legislative, local, and turnout), each with counts at every grain (any results, by constituency or county, finer, and map shapes; for the US split before 2000 and from 2000, with precinct results split into 2000–2015 and 2016 on; precinct shapes are their own tier, counting each precinct once per state and year, deduplicated across sources, with the target from 2016) for the tier and for each kind of election in it, plus the cited sources of the denominators and what is left uncounted; complete includes upcoming, the races still to vote that the archive holds with their candidates or options in full, and pending counts those still short of them; classes lists every census class of election by tier, including those with nothing held, with its status (held, queued, blocked, out of scope, or open), the reason for blocked and out-of-scope classes, the share counted, and the next scheduled date",
      }),
    )
    .input(v.objectAsync({ polity: Code }))
    .output(
      v.object({
        builtAt: v.nullable(v.string()),
        building: v.boolean(),
        commit: v.nullable(v.string()),
        stale: v.boolean(),
        summary: Summary,
        classes: v.array(CensusClass),
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
      const [status, [summary], classes] = await Promise.all([
        build(),
        summaries(input.polity),
        censusClasses(input.polity),
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
          g.known_gap as "knownGap", g.blocked, g.inapplicable,
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
            knownGap: 0,
            blocked: 0,
            inapplicable: 0,
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
          const sorted = [...byYear].toSorted((a, b) => a[0] - b[0]);
          const best = tightestHalf(sorted);
          return best == null ? [] : [{ tier, goal, ...best }];
        }),
      );
      return {
        ...status,
        summary,
        classes: classes.map(([, row]) => row),
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
      v.objectAsync({
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
          else r.total - r.complete - r."knownGap" - r.blocked end desc, r.key desc
      `);
      return found.rows;
    }),
  governments: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/{polity}/governments",
        summary:
          "US local governments of one kind in one state, from the Census of Governments: each government's place page, population, the latest year the archive holds its results, its next election date (with the scheduled election, when the archive has one), the statute or charter that sets its election cycle, and where its results are published and how a script can reach them; with a year, only the governments due to vote that year, those the archive lacks first; at most 500, largest first",
      }),
    )
    .input(
      v.objectAsync({
        polity: Code,
        state: v.pipe(v.string(), v.regex(/^[A-Z]{2}$/)),
        level: v.pipe(v.string(), v.maxLength(40)),
        year: v.optional(
          v.pipe(v.number(), v.integer(), v.minValue(1789), v.maxValue(2200)),
        ),
      }),
    )
    .output(
      v.object({
        total: v.number(),
        rows: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            path: v.nullable(v.string()),
            population: v.nullable(v.number()),
            missing: v.nullable(v.boolean()),
            last: v.nullable(v.number()),
            next: v.nullable(
              v.object({ date: v.string(), path: v.nullable(v.string()) }),
            ),
            cadence: v.nullable(
              v.object({
                title: v.string(),
                url: v.nullable(v.string()),
                locator: v.nullable(v.string()),
              }),
            ),
            access: v.nullable(
              v.object({
                platform: v.string(),
                url: v.nullable(v.string()),
                access: v.picklist(ACCESS),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const year = input.year ?? null;
      const found = await db.execute<{
        total: number;
        id: string;
        name: string;
        path: string | null;
        population: number | null;
        missing: boolean | null;
        last: number | null;
        next: { date: string; path: string | null } | null;
        cadence: {
          title: string;
          url: string | null;
          locator: string | null;
        } | null;
        access: {
          platform: string;
          url: string | null;
          access: (typeof ACCESS)[number];
        } | null;
      }>(sql`
        select count(*) over ()::int as total, g.id, g.name, g.population, y.missing,
          (select s.path from slug s where s.entity_id = g.polity_id and s.is_current limit 1) as path,
          (select max(x.year) from coverage_government_year x where x.government_id = g.id and x.held > 0) as last,
          case when g.next_date is not null then jsonb_build_object('date', g.next_date::text,
            'path', (select s.path from slug s where s.entity_id = g.next_election_id and s.is_current limit 1)) end as next,
          case when c.id is not null then jsonb_build_object('title', coalesce(cs.title, c.locator, ''), 'url', cs.url,
            'locator', nullif(c.locator, cs.title)) end as cadence,
          case when a.id is not null then jsonb_build_object('platform', a.platform,
            'url', case when a.url_pattern !~ '[{}]' then a.url_pattern end, 'access', a.access) end as access
        from coverage_government g
          left join coverage_government_year y on y.government_id = g.id and y.year = ${year}::int
          left join election_cadence c on c.id = g.cadence_id
          left join source cs on cs.id = c.source_id
          left join results_access a on a.id = g.access_id
        where ${input.polity} = 'us' and g.state = ${input.state} and g.kind = ${input.level}
          and case when ${year}::int is null then g.active else y.expected end
        order by y.missing desc nulls last, g.population desc nulls last, g.name, g.id
        limit 500
      `);
      return {
        total: found.rows[0]?.total ?? 0,
        rows: found.rows.map(({ total: _, ...row }) => row),
      };
    }),
  known: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage/known",
        summary:
          "One country's known gaps (polity, or id for the country's own polity), or those touching one contest or election (id): what the archive cannot fill yet and why, each with its category (known_gap: every source tried is exhausted; blocked: a source exists but cannot be reached yet; not_applicable: the grain does not exist), the period, office level, state, and grain it covers, the reason, the date checked, how many races it touches, the affected elections, and the number of the open bounty for it, if any",
      }),
    )
    .input(
      v.objectAsync({
        polity: v.optionalAsync(Code),
        id: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.array(
        v.object({
          polity: v.string(),
          goal: v.string(),
          level: v.string(),
          state: v.nullable(v.string()),
          stage: v.nullable(v.string()),
          from: v.nullable(v.number()),
          to: v.nullable(v.number()),
          label: v.nullable(v.string()),
          category: v.picklist(CATEGORIES),
          note: v.string(),
          checked: v.nullable(v.string()),
          count: v.number(),
          bounty: v.nullable(v.number()),
          elections: v.array(
            v.object({
              path: v.string(),
              date: v.nullable(v.string()),
              place: v.nullable(v.string()),
            }),
          ),
          more: v.number(),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const polity = input.polity ?? null;
      const id = input.id ?? null;
      if (polity == null && id == null) {
        throw new ORPCError("BAD_REQUEST");
      }
      const found = await db.execute<{
        polity: string;
        goal: string;
        level: string;
        state: string | null;
        stage: string | null;
        from: number | null;
        to: number | null;
        label: string | null;
        category: (typeof CATEGORIES)[number];
        note: string;
        checked: string | null;
        count: number;
        bounty: number | null;
        elections: Array<{
          path: string;
          date: string | null;
          place: string | null;
        }>;
        more: number;
      }>(sql`
        select c.polity, n ->> 'goal' as goal, n ->> 'level' as level, n ->> 'state' as state, n ->> 'stage' as stage,
          (n ->> 'from')::int as "from", (n ->> 'to')::int as "to", n ->> 'label' as label, n ->> 'category' as category,
          n ->> 'note' as note, n ->> 'checked' as checked,
          coalesce((n ->> 'count')::int, 0) as count,
          (select b.number from bounty b where b.gap_key = n ->> 'gap' and b.status = 'open') as bounty,
          coalesce((select jsonb_agg(jsonb_build_object('path', x.path, 'date', x.date, 'place', x.place) order by x.date, x.path)
            from (select s.path, e.date_from::text as date,
                (select pn.text from name pn where pn.entity_id = e.polity_id and pn.is_preferred limit 1) as place
              from jsonb_array_elements_text(coalesce(n -> 'elections', '[]')) a (eid)
              join election e on e.id = a.eid::uuid
              join slug s on s.entity_id = e.id and s.is_current
              order by e.date_from, s.path limit 12) x), '[]') as elections,
          greatest(jsonb_array_length(coalesce(n -> 'elections', '[]')) - 12, 0) as more
        from coverage_polity c cross join jsonb_array_elements(c.notes) with ordinality as t (n, i)
          cross join (select coalesce(${polity}::text, (select lower(p.iso_code) from polity p
            where p.id::text = ${id}::uuid::text and p.kind in ('sovereign_state', 'supranational'))) as cc) w
        where n ? 'category'
          and case when w.cc is not null then c.polity = w.cc and (coalesce((n ->> 'count')::int, 0) > 0
              or n ->> 'category' = 'not_applicable' or n ->> 'goal' in ('dates', 'party'))
            else coalesce(n -> 'contests', '[]') ? ${id}::uuid::text or coalesce(n -> 'elections', '[]') ? ${id}::uuid::text end
        order by c.polity, (n ->> 'from')::int nulls first, n ->> 'level', n ->> 'goal', i
      `);
      return found.rows;
    }),
};
