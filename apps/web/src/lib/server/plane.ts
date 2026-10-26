import registry from "@ea/db/projections";
import { sql } from "drizzle-orm";

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
