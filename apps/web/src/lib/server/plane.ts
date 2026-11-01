import registry from "@ea/db/projections";
import { sql } from "drizzle-orm";
import { remember } from "#lib/server/cache.js";
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
        name: string;
        within: Array<Array<number>>;
        clip?: Array<number>;
        srid: number;
        affine: Array<number>;
        detached?: boolean;
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

export const projectionOf = (scheme: string) =>
  projections[schemes[scheme.split(".", 1)[0] ?? ""] ?? fallback];

const insetsOf = (scheme: string) => projectionOf(scheme)?.insets ?? [];

const part = (scheme: string, column: string) =>
  sql.raw(
    `(select case ${insetsOf(scheme)
      .map((inset, index) => `when ${inside(inset.within)} then ${index}`)
      .join(
        " ",
      )} else -1 end from ST_Dump(${column}) d cross join lateral (select (ST_XMin(d.geom) + ST_XMax(d.geom)) / 2 as x, (ST_YMin(d.geom) + ST_YMax(d.geom)) / 2 as y) q order by ST_Area(d.geom) desc limit 1)`,
  );

export const outside = async (
  scheme: string,
  area: string,
  ks: Array<number>,
  day: string,
) => {
  if (ks.length === 0 || ks.length > 5000) {
    return [];
  }
  const insets = insetsOf(scheme);
  const found = await db.execute<{
    k: number;
    part: number;
    isAbroad: boolean;
    code: string | null;
  }>(sql`
    with me as materialized (
      select geom from area_geometry
      where area_id = ${area}::uuid
        and not exists (select from area_parent p where p.area_id = ${area}::uuid and p.valid @> ${day}::date)
      order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
    )
    select a.k, ${insets.length === 0 ? sql`-1` : part(scheme, "g.geom")} as part,
      not ST_Covers(me.geom, ST_PointOnSurface(g.geom))
        and (not ST_Intersects(me.geom, g.geom)
          or ST_Area(ST_Intersection(ST_MakeValid(ST_ClipByBox2D(me.geom, Box2D(g.geom))), g.geom)) < 0.5 * ST_Area(g.geom)) as "isAbroad",
      case when a.scheme_code = 'fr.departement' then (
        select c.code from area_code c where c.area_id = a.id
        order by c.valid @> ${day}::date desc limit 1
      ) end as code
    from area a
    cross join me
    cross join lateral (
      select g.geom from area_geometry g where g.area_id = a.id
      order by g.valid @> ${day}::date desc, upper(g.valid) desc nulls first limit 1
    ) g
    where a.k = any(${sql.param(ks)}::int[])
      and not (${sql.raw(
        (projectionOf(scheme)?.within ?? [])
          .map(
            ([west, south, east, north]) =>
              `g.geom @ ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)`,
          )
          .join(" or ") || "false",
      )})
  `);
  return found.rows.flatMap((row) => {
    const group: "overseas" | "abroad" | null =
      insets[row.part]?.detached === true
        ? "overseas"
        : row.part === -1 && row.isAbroad
          ? "abroad"
          : null;
    return group == null
      ? []
      : [{ k: row.k, group, ...(row.code != null && { code: row.code }) }];
  });
};

const live = async (scheme: string, contest: string, day: string) =>
  remember(`live:${scheme}:${contest}:${day}`, async () => {
    const found = await db.execute<{ part: number }>(sql`
      with recursive tree(id, depth) as (
        select c.id, 1 from contest c where c.allocation_parent_id = ${contest}::uuid
        union all
        select c.id, tree.depth + 1 from contest c join tree on c.allocation_parent_id = tree.id
        where tree.depth < 3
      )
      select distinct ${part(scheme, "g.geom")} as part
      from (select distinct id from tree) t
      join contest c on c.id = t.id
      cross join lateral (
        select g.geom from area_geometry g where g.area_id = c.district_area_id
        order by g.valid @> ${day}::date desc, upper(g.valid) desc nulls first limit 1
      ) g
    `);
    return found.rows.map((row) => row.part);
  });

export const parted = async (
  scheme: string,
  area: string,
  day: string,
  contest: { id: string; ks: Array<number> } | null = null,
) => {
  const insets = insetsOf(scheme);
  if (insets.length === 0) {
    return;
  }
  const [rows, own, held] = await Promise.all([
    remember(`parted:${scheme}:${day}`, async () => {
      const found = await db.execute<{
        k: number;
        part: number;
        x: number;
        y: number;
      }>(sql`
        select a.k, ${part(scheme, "g.geom")} as part, ST_X(o.point) as x, ST_Y(o.point) as y
        from (
          select g.area_id, g.geom from area_geometry g
          where (${sql.raw(
            insets
              .flatMap((inset) => inset.within)
              .map(
                ([west, south, east, north]) =>
                  `g.geom && ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)`,
              )
              .join(" or "),
          )}) and g.valid @> ${day}::date
          offset 0
        ) g
        join area a on a.id = g.area_id
        cross join lateral (select ST_PointOnSurface(g.geom) as point) o
        where a.scheme_code = ${scheme}
      `);
      return found.rows;
    }),
    db.execute<{ mine: number | null }>(sql`
      select ${part(scheme, "me.geom")} as mine from (
        select geom from area_geometry where area_id = ${area}::uuid
        order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
      ) me
    `),
    contest == null ? null : live(scheme, contest.id, day),
  ]);
  if (rows.length === 0 || own.rows.length === 0) {
    return;
  }
  const mine = own.rows[0]?.mine ?? -1;
  if (mine !== -1) {
    return {
      isOnly: true,
      ks: rows.filter((row) => row.part === mine).map((row) => row.k),
      inset: null,
    };
  }
  const outer = rows.filter((row) => row.part !== -1);
  const inner =
    outer.length === 0
      ? new Set<number>()
      : new Set(
          (
            await db.execute<{ k: number }>(sql`
              select o.k from unnest(
                ${sql.param(outer.map((row) => row.k))}::int[],
                ${sql.param(outer.map((row) => row.x))}::float8[],
                ${sql.param(outer.map((row) => row.y))}::float8[]
              ) o(k, x, y)
              cross join (
                select geom from area_geometry where area_id = ${area}::uuid
                order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
              ) me
              where ST_Covers(me.geom, ST_Point(o.x, o.y, 4326))
            `)
          ).rows.map((row) => row.k),
        );
  const counted = new Set(contest?.ks);
  const alive =
    held == null
      ? null
      : new Set([
          ...held,
          ...outer.filter((row) => counted.has(row.k)).map((row) => row.part),
        ]);
  const isAlive = (part: number) => alive == null || alive.has(part);
  const ks = outer
    .filter(
      (row) =>
        !inner.has(row.k) ||
        (!isAlive(row.part) && insets[row.part]?.detached !== true),
    )
    .map((row) => row.k);
  const boxed = outer
    .filter(
      (row) =>
        inner.has(row.k) &&
        isAlive(row.part) &&
        insets[row.part]?.detached !== true,
    )
    .map((row) => row.k);
  return ks.length === 0 && boxed.length === 0
    ? undefined
    : {
        isOnly: false,
        ks,
        inset: boxed.length === 0 ? null : { scheme, day, ks: boxed },
      };
};

export const boxes = async (scheme: string, day: string, ks: Array<number>) => {
  const insets = insetsOf(scheme);
  if (insets.length === 0 || ks.length === 0) {
    return [];
  }
  const found = await db.execute<{
    k: number;
    part: number;
    d: string;
    x: number;
    y: number;
    width: number;
    height: number;
  }>(sql`
    with picked as materialized (
      select a.k, a.scheme_code as scheme, g.geom as raw, coalesce(
        (select c.geom from display.clipped c
          join display.meta dm on dm.mask_version = c.mask_version
          where c.area_id = a.id and c.valid = g.valid and c.geom_hash = md5(ST_AsBinary(g.geom))),
        g.geom) as geom
      from area a
      cross join lateral (
        select geom, valid from area_geometry where area_id = a.id
        order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
      ) g
      where a.k = any(${sql.param(ks)}::int[]) and a.scheme_code = ${scheme}
    ),
    planed as materialized (
      select k, ${part(scheme, "raw")} as part, ${plane("geom", "scheme")} as geom from picked
    ),
    framed as (
      select k, part, geom,
        min(ST_XMin(geom)) over w as west, max(ST_XMax(geom)) over w as east,
        min(ST_YMin(geom)) over w as south, max(ST_YMax(geom)) over w as north
      from planed where part <> -1 and geom is not null
      window w as (partition by part)
    )
    select k, part, ST_AsSVG(ST_Scale(coalesce((
        select ST_Collect(d.geom) from ST_Dump(ST_SimplifyPreserveTopology(geom, span / 500)) d
        where ST_Area(d.geom) >= (span / 500) ^ 2
      ), ST_SimplifyPreserveTopology(geom, span / 500)), 0.001, 0.001), 0, 1) as d,
      west / 1000 as x, -north / 1000 as y, (east - west) / 1000 as width, (north - south) / 1000 as height
    from (select *, greatest(east - west, north - south) as span from framed) f
    order by part, k
  `);
  const parts = new Map<
    number,
    {
      name: string;
      x: number;
      y: number;
      width: number;
      height: number;
      units: Array<{ k: number; d: string }>;
    }
  >();
  for (const row of found.rows) {
    const seen = parts.get(row.part);
    if (seen == null) {
      parts.set(row.part, {
        name: insets[row.part]?.name ?? "",
        x: row.x,
        y: row.y,
        width: row.width,
        height: row.height,
        units: [{ k: row.k, d: row.d }],
      });
    } else {
      seen.units.push({ k: row.k, d: row.d });
    }
  }
  return [...parts.values()];
};
