import { call } from "@orpc/server";
import { sql } from "drizzle-orm";
import type { Seed } from "#lib/live/simulate.js";
import type { Geography } from "#lib/live/standing.js";
import { remember } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import america from "#lib/server/hex/us-cd.json";
import { plane, spot } from "#lib/server/plane.js";
import { names } from "#lib/server/rpc/archive.js";
import { router } from "#lib/server/rpc/router.js";

export async function geography(
  areaIds: Array<string>,
  date: string,
  framed: Array<string> = areaIds,
  isOutlined = true,
): Promise<Geography | null> {
  if (areaIds.length === 0) {
    return null;
  }
  const found = await db.execute<{
    id: string;
    k: number;
    scheme: string;
    x: number | null;
    y: number | null;
  }>(sql`
    select distinct on (a.id) a.id, a.k, a.scheme_code as scheme, ST_X(p) as x, ST_Y(p) as y
    from area a join area_geometry g on g.area_id = a.id and g.valid @> ${date}::date
    cross join lateral (select ${spot("coalesce(g.label_point, ST_PointOnSurface(g.geom))", "a.scheme_code")} as p) at
    where a.id = any(${sql.param(areaIds)}::uuid[])
    order by a.id, g.valid_from desc nulls last
  `);
  const scheme = found.rows[0]?.scheme;
  if (scheme == null) {
    return null;
  }
  const box = await db.execute<{
    tiles: string;
    west: number;
    south: number;
    east: number;
    north: number;
  }>(sql`
    select coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles,
      st_xmin(x.box) as west, st_ymin(x.box) as south, st_xmax(x.box) as east, st_ymax(x.box) as north
    from boundary_set bs, lateral (
      select ST_Transform(ST_SetSRID(ST_Extent(${plane("g.geom", "a.scheme_code")})::geometry, 3857), 4326) as box
      from area_geometry g join area a on a.id = g.area_id where g.area_id = any(${sql.param(framed)}::uuid[]) and g.valid @> ${date}::date
    ) x
    where bs.scheme_code = ${scheme} and bs.valid @> ${date}::date
    order by bs.valid_from desc nulls last limit 1
  `);
  const row = box.rows[0];
  if (row == null) {
    return null;
  }
  const drawn = isOutlined
    ? await db.execute<{ rings: Array<Array<[number, number]>> | null }>(sql`
      select (
        select json_agg((ST_AsGeoJSON(ST_ExteriorRing(e.geom), 6)::json)->'coordinates')
        from ST_Dump(ST_ForcePolygonCW((
          select ST_Union(ST_MakeValid(ST_MakePolygon(ST_ExteriorRing(d.geom))))
          from ST_Dump(ST_Transform(ST_SetSRID(ST_Simplify(
            ST_Buffer(ST_SimplifyPreserveTopology(q.geom, q.t), q.t * 2, 'quad_segs=2'), q.t
          ), 3857), 4326)) d
        ))) e
      ) as rings
      from (
        select p.geom, least(sqrt(ST_Area(p.geom)) / 2000, 250) as t from (
          select ST_Union(ST_SimplifyPreserveTopology(${plane("g.geom", "a.scheme_code")}, 50)) as geom
          from area_geometry g join area a on a.id = g.area_id
          where g.area_id = any(${sql.param(framed)}::uuid[]) and g.valid @> ${date}::date
        ) p
      ) q
    `)
    : null;
  return {
    tiles: row.tiles,
    scheme: scheme.split(".", 1)[0] ?? scheme,
    outline: drawn?.rows[0]?.rings ?? [],
    frame: {
      west: row.west,
      south: row.south,
      east: row.east,
      north: row.north,
    },
    places: found.rows.flatMap((place) =>
      place.x == null || place.y == null
        ? []
        : [
            {
              areaId: place.id,
              k: place.k,
              at: [place.x, place.y] as [number, number],
            },
          ],
    ),
  };
}

export async function states(date: string) {
  const found = await db.execute<{ code: string; id: string }>(sql`
    select lower(split_part(p.iso_code, '-', 2)) as code, a.id
    from polity p join polity_territory t on t.polity_id = p.id and t.valid @> ${date}::date
    join area a on a.id = t.area_id and a.scheme_code = 'us.state'
    where p.iso_code like 'US-%'
  `);
  const ids = found.rows.map((row) => row.id);
  const [map, named] = await Promise.all([
    geography(ids, date, ids, false),
    names(ids, date),
  ]);
  return map == null
    ? null
    : {
        map,
        codes: Object.fromEntries(found.rows.map((row) => [row.code, row.id])),
        labels: Object.fromEntries(
          found.rows.map((row) => [
            row.code,
            named.short.get(row.id) ??
              named.full.get(row.id) ??
              row.code.toUpperCase(),
          ]),
        ),
      };
}

const LAYOUTS = import.meta.glob<{
  districts: Record<string, string>;
  bounds: Record<string, [number, number, number, number]>;
  points: Record<string, [number, number]>;
  states: Record<string, string>;
  labels: Record<string, [number, number, string, number?]>;
}>("./hex/us-cd-[0-9]*.json", { import: "default" });

export async function house(electionId: string, date: string) {
  const found = await db.execute<{ id: string; code: string }>(sql`
    with recursive tree(id) as (
      select ${electionId}::uuid
      union all
      select e.id from election e join tree on e.parent_id = tree.id where e.kind not in ('runoff', 'indirect')
    )
    select distinct a.id, ac.code from contest c join tree on tree.id = c.election_id
    join area a on a.id = c.district_area_id and a.scheme_code = 'us.cd'
    join area_code ac on ac.area_id = a.id
  `);
  const ids = found.rows.map((row) => row.id);
  const load =
    LAYOUTS[`./hex/us-cd-${america[date as keyof typeof america] ?? ""}.json`];
  const [map, layout] = await Promise.all([
    geography(ids, date, ids, false),
    load == null ? null : load(),
  ]);
  const postal = new Map(
    Object.entries(layout?.labels ?? {}).map(([fips, label]) => [
      fips,
      label[2].toLowerCase(),
    ]),
  );
  const key = (code: string) =>
    `${postal.get(code.slice(0, 2)) ?? code.slice(0, 2)}-${Number(code.slice(2))}`;
  const codes = Object.fromEntries(
    found.rows.map((row) => [key(row.code), row.id]),
  );
  const kept = Object.keys(layout?.districts ?? {}).filter(
    (code) => codes[key(code)] != null,
  );
  const fips = new Set(kept.map((code) => code.slice(0, 2)));
  const boxes = kept.flatMap((code) => {
    const box = layout?.bounds[code];
    return box == null ? [] : [box];
  });
  return {
    map,
    codes,
    cartogram:
      layout == null || boxes.length === 0
        ? null
        : {
            box: [
              Math.min(...boxes.map((box) => box[0])),
              Math.min(...boxes.map((box) => box[1])),
              Math.max(...boxes.map((box) => box[2])),
              Math.max(...boxes.map((box) => box[3])),
            ] as [number, number, number, number],
            districts: kept.flatMap((code) => {
              const d = layout.districts[code];
              const point = layout.points[code];
              return d == null || point == null
                ? []
                : [{ key: key(code), d, x: point[0], y: point[1] }];
            }),
            states: Object.entries(layout.states).flatMap(([state, d]) =>
              fips.has(state) ? [d] : [],
            ),
            labels: Object.entries(layout.labels).flatMap(
              ([state, [x, y, text]]) =>
                fips.has(state) ? [{ x, y, text }] : [],
            ),
          },
  };
}

const MOCK = {
  path: "us/mi/2026-11-03-general",
  close: "2026-11-04T01:00:00Z",
  central: /^(?:gogebic|iron|dickinson|menominee)\b/iu,
};

async function seed(): Promise<{ seed: Seed; geography: Geography | null }> {
  const { election } = await call(router.live.election, { path: MOCK.path });
  const race = election.races.find((entry) => entry.office === "senate");
  if (race == null) {
    throw new Error("no senate race");
  }
  const closes: Record<string, number> = {};
  for (const unit of race.units) {
    closes[unit.id] =
      Date.parse(MOCK.close) + (MOCK.central.test(unit.name) ? 3_600_000 : 0);
  }
  return {
    seed: {
      election: {
        id: election.id,
        slug: election.slug,
        title: election.title,
        date: election.date,
        pollClose: [{ state: "mi", at: MOCK.close }],
      },
      race: { ...race, frames: [], counts: [], certified: null },
      closes,
    },
    geography: await geography(
      race.units.flatMap((unit) => (unit.areaId == null ? [] : [unit.areaId])),
      election.date,
    ),
  };
}

export async function mock() {
  return remember("live:mock:v2:mi-senate", seed);
}
