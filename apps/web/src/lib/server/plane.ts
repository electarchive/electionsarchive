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
    isOver: boolean;
    code: string | null;
  }>(sql`
    with me as materialized (
      select geom from area_geometry
      where area_id = ${area}::uuid
        and not exists (select from area_parent p where p.area_id = ${area}::uuid and p.valid @> ${day}::date)
      order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
    ),
    drawn as materialized (
      select a.k, a.id, a.scheme_code, g.geom, ST_PointOnSurface(g.geom) as point, ST_Area(g.geom) as size, g.valid @> ${day}::date as "isCurrent"
      from area a
      cross join lateral (
        select g.geom, g.valid from area_geometry g where g.area_id = a.id
        order by g.valid @> ${day}::date desc, upper(g.valid) desc nulls first limit 1
      ) g
      where a.k = any(${sql.param(ks)}::int[])
    ),
    flagged as (
      select d.*, d."isCurrent" and exists (
        select from drawn o
        where o.k <> d.k and o."isCurrent" and o.size < d.size and o.point && d.geom and ST_Covers(d.geom, o.point)
      ) as "isOver"
      from drawn d
    )
    select a.k, ${insets.length === 0 ? sql`-1` : part(scheme, "a.geom")} as part, a."isOver",
      not ST_Covers(me.geom, a.point)
        and (not ST_Intersects(me.geom, a.geom)
          or ST_Area(ST_Intersection(ST_MakeValid(ST_ClipByBox2D(me.geom, Box2D(a.geom))), a.geom)) < 0.5 * ST_Area(a.geom)) as "isAbroad",
      case when a.scheme_code = 'fr.departement' then (
        select c.code from area_code c where c.area_id = a.id
        order by c.valid @> ${day}::date desc limit 1
      ) end as code
    from flagged a
    cross join me
    where a."isOver"
      or not (${sql.raw(
        (projectionOf(scheme)?.within ?? [])
          .map(
            ([west, south, east, north]) =>
              `a.geom @ ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)`,
          )
          .join(" or ") || "false",
      )})
  `);
  return found.rows.flatMap((row) => {
    const group: "territory" | "overseas" | "abroad" | "over" | null =
      row.isOver
        ? "over"
        : insets[row.part]?.detached === true
          ? scheme.startsWith("us.")
            ? "territory"
            : "overseas"
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
  if (own.rows.length === 0) {
    return;
  }
  const mine = own.rows[0]?.mine ?? -1;
  const isApart =
    mine === -1 && insets.some((inset) => inset.detached !== true);
  if (rows.length === 0) {
    return isApart
      ? { isOnly: false, isApart, ks: [], inset: null }
      : undefined;
  }
  if (mine !== -1) {
    return {
      isOnly: true,
      isApart,
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
  return ks.length === 0 && boxed.length === 0 && !isApart
    ? undefined
    : {
        isOnly: false,
        isApart,
        ks,
        inset: boxed.length === 0 ? null : { scheme, day, ks: boxed },
      };
};

export const boxes = async (
  asks: Array<{ scheme: string; day: string; ks: Array<number> }>,
) => {
  const scheme = asks[0]?.scheme ?? "";
  const insets = insetsOf(scheme);
  const kept = asks.filter(
    (ask) => projectionOf(ask.scheme) === projectionOf(scheme),
  );
  const ks = kept.flatMap((ask) => ask.ks);
  if (insets.length === 0 || ks.length === 0) {
    return [];
  }
  const found = await db.execute<{
    k: number;
    part: number;
    d: string;
    width: number;
    height: number;
  }>(sql`
    with picked as materialized (
      select a.k, a.scheme_code as scheme, g.geom as raw, ST_Simplify(coalesce(
        (select c.geom from display.clipped c
          join display.meta dm on dm.mask_version = c.mask_version
          where c.area_id = a.id and c.valid = g.valid and c.geom_hash = md5(ST_AsBinary(g.geom))),
        g.geom), greatest(ST_XMax(g.geom) - ST_XMin(g.geom), ST_YMax(g.geom) - ST_YMin(g.geom)) / 2000) as geom
      from unnest(
        ${sql.param(ks)}::int[],
        ${sql.param(kept.flatMap((ask) => ask.ks.map(() => ask.day)))}::date[],
        ${sql.param(kept.flatMap((ask) => ask.ks.map(() => ask.scheme)))}::text[]
      ) w(k, day, scheme)
      join area a on a.k = w.k and a.scheme_code = w.scheme
      cross join lateral (
        select geom, valid from area_geometry where area_id = a.id
        order by valid @> w.day desc, upper(valid) desc nulls first limit 1
      ) g
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
    ),
    thin as materialized (
      select k, part, west, east, south, north, span, d.geom,
        greatest(ST_XMax(d.geom) - ST_XMin(d.geom), ST_YMax(d.geom) - ST_YMin(d.geom)) >= span / 40 as wide
      from (select *, greatest(east - west, north - south) as span from framed) f,
        ST_Dump(ST_SimplifyPreserveTopology(f.geom, f.span / 500)) d
    ),
    land as materialized (
      select part, geom from thin where wide
    ),
    shaped as materialized (
      select t.*, case
        when t.wide then 'land'
        when not exists (
          select 1 from land l where l.part = t.part and ST_DWithin(l.geom, t.geom, t.span / 40)
        ) then 'dot'
        when ST_Area(t.geom) >= (t.span / 500) ^ 2 then 'land'
      end as shape
      from thin t
    )
    select k, part, coalesce(ST_AsSVG(ST_Scale(ST_Translate(ST_Collect(array_remove(array[
        ST_Collect(geom) filter (where shape = 'land'),
        ST_Union(distinct ST_Buffer(ST_SnapToGrid(ST_Centroid(geom), span / 80), span / 80, 'quad_segs=2')) filter (where shape = 'dot')
      ], null)), -west, -north), 0.001, 0.001), 0, 1), '') as d,
      (east - west) / 1000 as width, (north - south) / 1000 as height
    from shaped
    group by k, part, west, east, south, north, span
    order by part, k
  `);
  const parts = new Map<
    number,
    {
      name: string;
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

type Measured = {
  k: number;
  id: string;
  x: number;
  y: number;
  west: number;
  south: number;
  east: number;
  north: number;
  size: number;
  lon: number;
  lat: number;
};

const clusters = (units: Array<Measured>, reach: number, least: number) => {
  const seen = new Set<number>();
  const found: Array<Array<Measured>> = [];
  const near = (unit: Measured) =>
    units.filter(
      (other) => Math.hypot(other.x - unit.x, other.y - unit.y) <= reach,
    );
  for (const unit of units) {
    if (seen.has(unit.k) || near(unit).length < least) {
      continue;
    }
    const group: Array<Measured> = [];
    const queue = [unit];
    seen.add(unit.k);
    for (let at = 0; at < queue.length; at++) {
      const next = queue[at];
      if (next == null) {
        continue;
      }
      group.push(next);
      const around = near(next);
      if (around.length < least) {
        continue;
      }
      for (const other of around) {
        if (!seen.has(other.k)) {
          seen.add(other.k);
          queue.push(other);
        }
      }
    }
    found.push(group);
  }
  return found;
};

const extent = (units: Array<Measured>) => {
  let [west, south, east, north] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const unit of units) {
    west = Math.min(west, unit.west);
    south = Math.min(south, unit.south);
    east = Math.max(east, unit.east);
    north = Math.max(north, unit.north);
  }
  return { west, south, east, north };
};

const padded = (box: ReturnType<typeof extent>) => {
  const span = Math.max(box.east - box.west, box.north - box.south);
  const pad = span * 0.08;
  let width = box.east - box.west + pad * 2;
  let height = box.north - box.south + pad * 2;
  width = Math.max(width, height * 0.6);
  height = Math.max(height, width / 1.8);
  const x = (box.west + box.east) / 2;
  const y = (box.south + box.north) / 2;
  return {
    west: x - width / 2,
    south: y - height / 2,
    east: x + width / 2,
    north: y + height / 2,
  };
};

const dense = (
  units: Array<Measured>,
  span: number,
  tiny: number,
  depth = 0,
): Array<Array<Measured>> =>
  clusters(
    units.filter((unit) => unit.size < span / tiny),
    span / (tiny * 0.55),
    5,
  ).flatMap((group) => {
    const box = extent(group);
    if (group.length < 6) {
      return [];
    }
    if (Math.max(box.east - box.west, box.north - box.south) < span / 7) {
      return [group];
    }
    return depth < 3 ? dense(group, span, tiny * 1.5, depth + 1) : [];
  });

const degrees = (x: number, y: number) => {
  const radius = 6_378_137;
  return [
    ((x / radius) * 180) / Math.PI,
    ((2 * Math.atan(Math.exp(y / radius)) - Math.PI / 2) * 180) / Math.PI,
  ] as const;
};

const reach = (box: ReturnType<typeof extent>) => {
  const [west, south] = degrees(box.west, box.south);
  const [east, north] = degrees(box.east, box.north);
  return (east - west) * (north - south) * 10;
};

export const vintage = async (tiles: string) => {
  const set = await db.execute<{ scheme: string; day: string }>(sql`
    select scheme_code as scheme, as_of::text as day from boundary_set
    where coalesce(tiles_key, scheme_code || '/' || vintage) = ${tiles}
    order by as_of desc limit 1
  `);
  return set.rows[0];
};

export const zooms = async (tiles: string, ks: Array<number>) => {
  const none = { day: null, ids: [], zones: [] };
  if (ks.length < 24 || ks.length > 5000) {
    return none;
  }
  const held = await vintage(tiles);
  if (held == null) {
    return none;
  }
  const { scheme, day } = held;
  const clipped =
    "coalesce((select c.geom from display.clipped c join display.meta dm on dm.mask_version = c.mask_version where c.area_id = picked.id and c.valid = picked.valid and c.geom_hash = md5(ST_AsBinary(picked.raw))), picked.raw)";
  const picked = (wanted: Array<number>) => sql`
    picked as materialized (
      select a.k, a.id, a.scheme_code as scheme, g.geom as raw, g.valid
      from area a
      cross join lateral (
        select geom, valid from area_geometry where area_id = a.id
        order by valid @> ${day}::date desc, upper(valid) desc nulls first limit 1
      ) g
      where a.k = any(${sql.param(wanted)}::int[]) and a.scheme_code = ${scheme}
    )
  `;
  const measured = await db.execute<Measured & { part: number }>(sql`
    with ${picked(ks)},
    planed as materialized (
      select k, id, ${insetsOf(scheme).length === 0 ? sql`-1` : part(scheme, "raw")} as part,
        ${plane(`coalesce((select c.geom from display.coarse c where c.area_id = picked.id and c.valid = picked.valid), ${clipped})`, "scheme")} as geom,
        ST_PointOnSurface(raw) as spot
      from picked
    )
    select k, id::text, part, ST_XMin(geom) as west, ST_YMin(geom) as south,
      ST_XMax(geom) as east, ST_YMax(geom) as north, sqrt(ST_Area(geom)) as size,
      (ST_XMin(geom) + ST_XMax(geom)) / 2 as x, (ST_YMin(geom) + ST_YMax(geom)) / 2 as y,
      ST_X(spot) as lon, ST_Y(spot) as lat
    from planed where geom is not null and not ST_IsEmpty(geom)
  `);
  const main = measured.rows.filter((row) => row.part === -1);
  const whole = extent(main);
  const span = Math.max(whole.east - whole.west, whole.north - whole.south);
  if (main.length < 24 || !Number.isFinite(span)) {
    return none;
  }
  const inside = (box: ReturnType<typeof extent>) =>
    main.filter(
      (unit) =>
        unit.east > box.west &&
        unit.west < box.east &&
        unit.north > box.south &&
        unit.south < box.north,
    );
  const side = (units: Array<Measured>) => {
    const box = padded(extent(units));
    return Math.max(box.east - box.west, box.north - box.south);
  };
  const groups = dense(main, span, 80).map((units) => ({
    units,
    side: side(units),
  }));
  for (let at = 0; at < groups.length; at++) {
    for (let next = at + 1; next < groups.length; next++) {
      const [one, two] = [groups[at], groups[next]];
      if (one == null || two == null) {
        continue;
      }
      const [a, b] = [padded(extent(one.units)), padded(extent(two.units))];
      const units = [...one.units, ...two.units];
      const merged = side(units);
      if (
        a.west < b.east &&
        b.west < a.east &&
        a.south < b.north &&
        b.south < a.north &&
        merged <= Math.max(one.side, two.side) * 1.5 &&
        merged <= span / 7
      ) {
        groups[at] = { units, side: Math.max(one.side, two.side) };
        groups.splice(next, 1);
        next = at;
      }
    }
  }
  const need = (units: Array<Measured>) => {
    let total = 0;
    for (const unit of units) {
      total += Math.min(4, (span / 160 / unit.size) ** 2);
    }
    return total;
  };
  const found: Array<{
    core: Array<Measured>;
    box: ReturnType<typeof extent>;
    within: number | null;
  }> = [];
  for (const group of groups
    .map((group) => group.units)
    .filter((units) => need(units) >= 16)
    .toSorted((a, b) => need(b) - need(a))
    .slice(0, 3)) {
    const box = padded(extent(group));
    const at = found.length;
    found.push({ core: group, box, within: null });
    const inner = Math.max(box.east - box.west, box.north - box.south);
    const nested = dense(inside(box), inner, 30).toSorted(
      (a, b) => b.length - a.length,
    )[0];
    if (nested != null && nested.length >= 10 && nested.length < group.length) {
      found.push({ core: nested, box: padded(extent(nested)), within: at });
    }
  }
  if (found.length === 0) {
    return none;
  }
  const frames = found.map((entry, index) => ({
    index,
    ...entry.box,
    ks: inside(entry.box).map((unit) => unit.k),
  }));
  const [drawn, lineage] = await Promise.all([
    db.execute<{ index: number; k: number; d: string }>(sql`
      with ${picked([...new Set(frames.flatMap((frame) => frame.ks))])},
      planed as materialized (
        select k, ${plane(clipped, "scheme")} as geom from picked
      ),
      frames as (
        select * from unnest(
          ${sql.param(frames.map((frame) => frame.index))}::int[],
          ${sql.param(frames.map((frame) => frame.west))}::float8[],
          ${sql.param(frames.map((frame) => frame.south))}::float8[],
          ${sql.param(frames.map((frame) => frame.east))}::float8[],
          ${sql.param(frames.map((frame) => frame.north))}::float8[]
        ) f(index, west, south, east, north)
      ),
      cut as (
        select f.index, p.k, f.west, f.north, greatest(f.east - f.west, f.north - f.south) as side,
          ST_CollectionExtract(ST_ClipByBox2D(p.geom, ST_MakeEnvelope(f.west, f.south, f.east, f.north, 3857)), 3) as geom
        from frames f
        join planed p on p.geom && ST_MakeEnvelope(f.west, f.south, f.east, f.north, 3857)
      ),
      thin as (
        select index, k, west, north, side,
          ST_SnapToGrid(ST_Scale(ST_Translate(
            ST_CoverageSimplify(geom, side / 360) over (partition by index),
          -west, -north), 600 / side, 600 / side), 1) as geom
        from cut where not ST_IsEmpty(geom)
      )
      select index, k, ST_AsSVG(geom, 1, 0) as d
      from thin where not ST_IsEmpty(geom)
      order by index, k
    `),
    db.execute<{
      index: number;
      place: string;
      covered: number;
      total: number;
      isNear: boolean;
      size: number;
      scheme: string;
    }>(sql`
      with cores as (
        select * from unnest(
          ${sql.param(found.map((_, index) => index))}::int[],
          ${sql.param(found.map((entry) => JSON.stringify(entry.core.map((unit) => [unit.lon, unit.lat]))))}::jsonb[],
          ${sql.param(found.map((entry) => reach(entry.box)))}::float8[]
        ) c(index, points, reach)
      ),
      spots as (
        select c.index, c.reach, jsonb_array_length(c.points) as total,
          ST_Collect(ST_Point((p ->> 0)::float8, (p ->> 1)::float8, 4326)) as points
        from cores c cross join jsonb_array_elements(c.points) p
        group by c.index, c.reach, c.points
      ),
      probed as (
        select distinct s.index, g.area_id, g.valid
        from spots s
        cross join lateral ST_Dump(s.points) p
        join area_geometry g on g.geom && p.geom and g.valid @> ${day}::date
        join area a on a.id = g.area_id and a.scheme_code <> ${scheme}
        join scheme sc on sc.code = a.scheme_code and sc.kind = 'administrative'
      )
      select s.index, g.area_id::text as place, s.total,
        ST_NumGeometries(ST_Intersection(coalesce((select c.geom from display.coarse c where c.area_id = g.area_id and c.valid = g.valid), g.geom), s.points)) as covered,
        ST_Area(Box2D(g.geom)) < s.reach as "isNear", ST_Area(g.geom) as size, a.scheme_code as scheme
      from probed p
      join spots s on s.index = p.index
      join area_geometry g on g.area_id = p.area_id and g.valid = p.valid
      join area a on a.id = p.area_id
      order by s.index, ST_Area(g.geom)
    `),
  ]);
  const placed = Map.groupBy(lineage.rows, (row) => row.index);
  const zones = found.map((entry, index) => {
    const candidates = placed.get(index) ?? [];
    const smallest = (rows: Array<(typeof candidates)[number]>) => {
      const least = rows[0]?.size ?? 0;
      return rows
        .filter((row) => row.size <= least * 1.02)
        .toSorted((a, b) => a.scheme.localeCompare(b.scheme))[0];
    };
    const near = candidates.filter((row) => row.isNear);
    const whole = smallest(near.filter((row) => row.covered * 2 >= row.total));
    const most = smallest(near.filter((row) => row.covered * 5 >= row.total));
    const broad = candidates.find(
      (row) => !row.isNear && row.covered * 2 >= row.total,
    );
    const [place, kind] =
      whole != null
        ? [whole.place, "exact" as const]
        : most != null
          ? [most.place, "around" as const]
          : [broad?.place ?? null, "inside" as const];
    const [west, south] = degrees(entry.box.west, entry.box.south);
    const [east, north] = degrees(entry.box.east, entry.box.north);
    return {
      place,
      kind,
      within: entry.within,
      core: entry.core.map((unit) => unit.k),
      cores: entry.core.map((unit) => unit.id),
      frame: { west, south, east, north },
      x: entry.box.west / 1000,
      y: -entry.box.north / 1000,
      width: (entry.box.east - entry.box.west) / 1000,
      height: (entry.box.north - entry.box.south) / 1000,
      units: drawn.rows
        .filter((row) => row.index === index)
        .map((row) => ({ k: row.k, d: row.d })),
    };
  });
  return { day, ids: main.map((unit) => unit.id), zones };
};
