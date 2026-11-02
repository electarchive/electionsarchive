import { call } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { remember } from "#lib/server/cache.js";
import { liveDb as db } from "#lib/server/db/index.js";
import { plane } from "#lib/server/plane.js";
import { router } from "#lib/server/rpc/router.js";
import type { RequestHandler } from "./$types";

async function shapes(path: string) {
  const { election } = await call(router.live.election, { path });
  const isNation = path.split("/").length === 2;
  const codes: Record<string, string> = {};
  if (isNation) {
    const found = await db.execute<{ code: string; id: string }>(sql`
      select lower(split_part(p.iso_code, '-', 2)) as code, a.id
      from polity p join polity_territory t on t.polity_id = p.id and t.valid @> ${election.date}::date
      join area a on a.id = t.area_id and a.scheme_code = 'us.state'
      where p.iso_code like 'US-%'
    `);
    for (const row of found.rows) {
      codes[row.code] = row.id;
    }
  }
  const ids = isNation
    ? Object.values(codes)
    : [
        ...new Set(
          election.races.flatMap((race) =>
            race.units.flatMap((unit) =>
              unit.areaId == null ? [] : [unit.areaId],
            ),
          ),
        ),
      ];
  if (ids.length === 0) {
    return { box: [0, 0, 0, 0] as const, areas: {}, codes };
  }
  const drawn = await db.execute<{
    id: string;
    d: string;
    width: number;
    height: number;
  }>(sql`
    with g as (
      select distinct on (a.id) a.id, ${plane("g.geom", "a.scheme_code")} as geom
      from area a join area_geometry g on g.area_id = a.id and g.valid @> ${election.date}::date
      where a.id = any(${sql.param(ids)}::uuid[])
      order by a.id, g.valid_from desc nulls last
    ), b as (
      select ST_XMin(e) as x, ST_YMax(e) as y, ST_XMax(e) - ST_XMin(e) as w, ST_YMax(e) - ST_YMin(e) as h
      from (select ST_Extent(geom)::geometry as e from g) q
    ), k as (
      select x, y, w, h, 1000 / greatest(w, h) as s from b
    )
    select g.id, ST_AsSVG(ST_Scale(ST_SimplifyPreserveTopology(ST_Translate(g.geom, -k.x, -k.y), 1.2 / k.s), k.s, k.s), 1, 1) as d,
      k.w * k.s as width, k.h * k.s as height
    from g cross join k
  `);
  const first = drawn.rows[0];
  return {
    box: [
      0,
      0,
      Math.ceil(first?.width ?? 0),
      Math.ceil(first?.height ?? 0),
    ] as const,
    areas: Object.fromEntries(drawn.rows.map((row) => [row.id, row.d])),
    codes,
  };
}

export const GET: RequestHandler = async ({ params, setHeaders }) => {
  const segments = params.path.split("/");
  if (segments.length < 2 || segments.length > 3) {
    error(404);
  }
  try {
    const found = await remember(`live:shapes:v2:${params.path}`, async () =>
      shapes(params.path),
    );
    setHeaders({ "cache-control": "public, max-age=900" });
    return Response.json(found);
  } catch {
    error(404);
  }
};
