import { openapi } from "@orpc/openapi";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";

const gap = v.nullable(v.number());
const row = v.object({
  path: v.string(),
  label: v.string(),
  kind: v.string(),
  expected: gap,
  held: v.number(),
  governments: gap,
  missing: gap,
  county: gap,
  precinct: gap,
  shapes: gap,
  precincts: v.number(),
  shaped: v.number(),
  countyTotal: gap,
  precinctTotal: gap,
  shapesTotal: gap,
  hasChildren: v.boolean(),
  href: v.nullable(v.string()),
  detail: v.nullable(v.string()),
});

export const coverage = {
  rows: os
    .meta(
      openapi({
        method: "GET",
        path: "/coverage",
        summary:
          "US coverage rows under a parent path: years, then Federal and each state, then offices and stages or local governments, with the gap counts behind each marker",
      }),
    )
    .input(
      v.object({ parent: v.optional(v.pipe(v.string(), v.maxLength(200))) }),
    )
    .output(v.object({ builtAt: v.nullable(v.string()), rows: v.array(row) }))
    .handler(async ({ input }) => {
      const parent = input.parent ?? null;
      const built = await db.execute<{ builtAt: string | null }>(sql`
        select built_at::text as "builtAt" from coverage_build
      `);
      const builtAt = built.rows[0]?.builtAt ?? null;
      const owners = await db.execute<{ kind: string; year: number }>(sql`
        select kind, year from coverage_row where path = ${parent}
      `);
      const owner = owners.rows[0];
      if (parent != null && owner?.kind === "governments") {
        const [, state = "", kind = ""] = parent.split("/", 3);
        const leaves = await db.execute<v.InferOutput<typeof row>>(sql`
          select 'g:' || g.id as path, g.name as label, 'government' as kind,
            y.expected::int as expected, y.held, null::int as governments,
            y.missing::int as missing, null::int as county,
            case when y.year >= 2000 then y.held - y.precinct + (y.expected and y.held = 0)::int end as precinct,
            case when y.year >= 2000 and y.precincts > 0 then y.precincts - y.shaped end as shapes,
            y.precincts, y.shaped, null::int as "countyTotal",
            case when y.year >= 2000 then y.held + (y.expected and y.held = 0)::int end as "precinctTotal",
            case when y.year >= 2000 and y.precincts > 0 then y.precincts end as "shapesTotal",
            false as "hasChildren", s.path as href,
            nullif(concat_ws(', ', g.function, case when g.kind <> 'county' then (
              select n.text from external_id c join name n on n.entity_id = c.entity_id and n.is_preferred
              where c.scheme = 'us.polity' and c.value = 'county:' || g.county
              order by n.lang <> 'en' limit 1
            ) end), '') as detail
          from coverage_government g
          join coverage_government_year y on y.government_id = g.id and y.year = ${owner.year}
          left join slug s on s.entity_id = g.polity_id and s.is_current
          where g.state = ${state} and g.kind = ${kind}
          order by g.name, g.id
        `);
        return { builtAt, rows: leaves.rows };
      }
      const found = await db.execute<v.InferOutput<typeof row>>(sql`
        select r.path, r.label, r.kind, r.expected, r.held, r.governments, r.missing, r.county,
          r.precinct, r.shapes, r.precincts, r.shaped, r.county_total as "countyTotal",
          r.precinct_total as "precinctTotal", r.shapes_total as "shapesTotal",
          r.kind = 'governments' or exists (select from coverage_row c where c.parent = r.path) as "hasChildren",
          null as href, null as detail
        from coverage_row r
        where r.parent is not distinct from ${parent}
        order by r.sort, r.label
      `);
      return { builtAt, rows: found.rows };
    }),
};
