import registry from "@ea/db/projections";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";

const {
  projections,
  schemes,
  fallback,
}: {
  projections: Record<
    string,
    {
      srid: number;
      within: Array<Array<number>>;
      insets?: Array<{
        within: Array<Array<number>>;
        clip?: Array<number>;
        srid: number;
        affine: Array<number>;
      }>;
    }
  >;
  schemes: Record<string, string>;
  fallback: string;
} = registry;

const inside = (boxes: Array<Array<number>>) =>
  boxes
    .map(
      ([west, south, east, north]) =>
        `(q.x between ${west} and ${east} and q.y between ${south} and ${north})`,
    )
    .join(" or ");

const branches = Object.entries(projections).flatMap(([name, entry]) => [
  ...(entry.insets ?? []).map((inset) => {
    const part =
      inset.clip == null
        ? "d.geom"
        : `ST_ClipByBox2D(d.geom, ST_MakeEnvelope(${inset.clip.join(", ")}, 4326))`;
    const [scale, x, y] = inset.affine;
    return `when q.p = '${name}' and (${inside(inset.within)}) then ST_SetSRID(ST_Affine(ST_Transform(${part}, ${inset.srid}), ${scale}, 0, 0, ${scale}, ${x}, ${y}), 3857)`;
  }),
  `when q.p = '${name}' and (${inside(entry.within)}) then ST_SetSRID(ST_Transform(d.geom, ${entry.srid}), 3857)`,
]);

const named = Object.entries(schemes)
  .map(([prefix, name]) => `when '${prefix}' then '${name}'`)
  .join(" ");

export const plane = (column: string, scheme: string) =>
  sql.raw(
    `(select ST_Collect(case ${branches.join(" ")} end) from ST_Dump(${column}) d cross join lateral (select (ST_XMin(d.geom) + ST_XMax(d.geom)) / 2 as x, (ST_YMin(d.geom) + ST_YMax(d.geom)) / 2 as y, case split_part(${scheme}, '.', 1) ${named} else '${fallback}' end as p) q)`,
  );

export const spot = (column: string, scheme: string) =>
  sql`ST_Transform(ST_GeometryN(${plane(column, scheme)}, 1), 4326)`;

export const parted = async (scheme: string, area: string, day: string) => {
  const insets =
    projections[schemes[scheme.split(".", 1)[0] ?? ""] ?? fallback]?.insets ??
    [];
  if (insets.length === 0) {
    return;
  }
  const part = (column: string) =>
    `(select case ${insets.map((inset, index) => `when ${inside(inset.within)} then ${index}`).join(" ")} else -1 end from ST_Dump(${column}) d cross join lateral (select (ST_XMin(d.geom) + ST_XMax(d.geom)) / 2 as x, (ST_YMin(d.geom) + ST_YMax(d.geom)) / 2 as y) q order by ST_Area(d.geom) desc limit 1)`;
  const found = await db.execute<{
    k: number;
    part: number;
    mine: number;
    isInside: boolean;
  }>(sql`
    with me as materialized (
      select geom from area_geometry where area_id = ${area}::uuid
      order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
    ),
    mine as materialized (select ${sql.raw(part("me.geom"))} as part from me)
    select a.k, ${sql.raw(part("g.geom"))} as part, mine.part as mine, ST_Covers(me.geom, ST_PointOnSurface(g.geom)) as "isInside"
    from area_geometry g
    join area a on a.id = g.area_id
    cross join me
    cross join mine
    where a.scheme_code = ${scheme} and g.valid @> ${day}::date
      and (${sql.raw(
        insets
          .flatMap((inset) => inset.within)
          .map(
            ([west, south, east, north]) =>
              `g.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)`,
          )
          .join(" or "),
      )})
  `);
  const mine = found.rows[0]?.mine ?? -1;
  const ks = found.rows
    .filter((row) =>
      mine === -1 ? row.part !== -1 && !row.isInside : row.part === mine,
    )
    .map((row) => row.k);
  return mine === -1 && ks.length === 0
    ? undefined
    : { isOnly: mine !== -1, ks };
};
