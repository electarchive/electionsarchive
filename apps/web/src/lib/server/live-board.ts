import { sql } from "drizzle-orm";
import type { Geography } from "#lib/live/standing.js";
import { liveDb as db } from "#lib/server/db/index.js";
import america from "#lib/server/hex/us-cd.json";
import { plane, spot } from "#lib/server/plane.js";
import { names } from "#lib/server/rpc/archive.js";

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
    select a.id, ac.code from area a join area_code ac on ac.area_id = a.id
    where a.scheme_code = 'us.cd'
      and exists (select from contest c join tree on tree.id = c.election_id where c.district_area_id = a.id)
    order by a.id, ac.code
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

const SIDES = new Set(["senate", "house", "governor"]);

export async function control(
  races: Array<{
    id: string;
    office: string;
    options: Array<{ party: { id: string } | null }>;
  }>,
  date: string,
) {
  const ids = races
    .filter((race) => SIDES.has(race.office))
    .map((race) => race.id);
  const [held, kept] = await Promise.all([
    db.execute<{ contest: string; person: string; party: string | null }>(sql`
      with up as materialized (
        select c.id, c.seat_id, c.office_id, c.body_id, c.district_area_id from contest c where c.id = any(${sql.param(ids)}::uuid[])
      ),
      sitting as materialized (
        select t.id, t.person_id, t.contest_id, t.seat_id, t.office_id, t.body_id, t.from_date, s.district_area_id
        from tenure t left join seat s on s.id = t.seat_id
        where t.from_date <= ${date}::date and (t.to_date is null or t.to_date > ${date}::date)
      ),
      held as (
        select distinct on (m.contest) m.contest, m.tenure, m.person, m.won from (
          select u.id as contest, t.id as tenure, t.person_id as person, t.contest_id as won, t.from_date
          from up u join sitting t on t.seat_id = u.seat_id
          union all
          select u.id, t.id, t.person_id, t.contest_id, t.from_date
          from up u join sitting t on t.office_id = u.office_id
          union all
          select u.id, t.id, t.person_id, t.contest_id, t.from_date
          from up u cross join lateral (
            select u.district_area_id as area_id
            union
            select x.area_id from area_code y join area_code x on x.code = y.code where y.area_id = u.district_area_id
          ) a
          join sitting t on t.body_id = u.body_id and t.district_area_id = a.area_id
          where u.seat_id is null and u.office_id is null
        ) m
        order by m.contest, m.from_date desc
      )
      select h.contest, h.person, coalesce(
        (select ta.party_id from tenure_affiliation ta where ta.tenure_id = h.tenure and ta.kind = 'party' and ta.valid @> ${date}::date limit 1),
        (select o.party_id from candidacy ca join contest_option o on o.id = ca.option_id where ca.person_id = h.person and o.party_id is not null
          order by (o.contest_id = h.won) desc nulls last, o.contest_id desc limit 1)
      ) as party
      from held h
    `),
    db.execute<{ office: string; person: string; party: string | null }>(sql`
      with up as (
        select c.seat_id, c.office_id from contest c where c.id = any(${sql.param(ids)}::uuid[])
      ),
      sitting as (
        select distinct on (coalesce(t.seat_id, t.office_id)) t.id, t.person_id, t.contest_id,
          case when b.kind = 'upper_house' then 'senate' else 'governor' end as office
        from tenure t
        left join body b on b.id = t.body_id
        left join office o on o.id = t.office_id
        left join polity bp on bp.id = b.polity_id
        left join polity op on op.id = o.polity_id
        where t.from_date <= ${date}::date and (t.to_date is null or t.to_date > ${date}::date)
          and ((b.kind = 'upper_house' and bp.iso_code = 'US' and t.seat_id not in (select seat_id from up where seat_id is not null))
            or (o.kind = 'governor' and op.iso_code ~ '^US-[A-Z]{2}$' and op.iso_code not in ('US-AS', 'US-DC', 'US-GU', 'US-MP', 'US-PR', 'US-VI') and t.office_id not in (select office_id from up where office_id is not null)))
        order by coalesce(t.seat_id, t.office_id), t.from_date desc
      )
      select s.office, s.person_id as person, coalesce(
        (select ta.party_id from tenure_affiliation ta where ta.tenure_id = s.id and ta.kind = 'party' and ta.valid @> ${date}::date limit 1),
        (select o.party_id from candidacy ca join contest_option o on o.id = ca.option_id where ca.person_id = s.person_id and o.party_id is not null
          order by (o.contest_id = s.contest_id) desc nulls last, o.contest_id desc limit 1)
      ) as party
      from sitting s
    `),
  ]);
  const parties = [
    ...new Set([
      ...held.rows.flatMap((row) => (row.party == null ? [] : [row.party])),
      ...kept.rows.flatMap((row) => (row.party == null ? [] : [row.party])),
      ...races.flatMap((race) =>
        race.options.flatMap((option) =>
          option.party == null ? [] : [option.party.id],
        ),
      ),
    ]),
  ];
  const parents = await db.execute<{ party: string; parent: string }>(sql`
    select party_id as party, parent_party_id as parent from party_affiliation
    where kind = 'regional_branch' and valid @> ${date}::date and party_id = any(${sql.param(parties)}::uuid[])
  `);
  const families: Record<string, string> = Object.fromEntries(
    parents.rows.map((row) => [row.party, row.parent]),
  );
  const family = (party: string | null) =>
    party == null ? null : (families[party] ?? party);
  const roots = [...new Set(parties.map((party) => family(party) ?? party))];
  const [named, people, colors] = await Promise.all([
    names(roots, date),
    names(
      [...held.rows, ...kept.rows].map((row) => row.person),
      date,
    ),
    db.execute<{ party: string; color: string }>(sql`
      select party_id as party, color from party_color
      where valid @> ${date}::date and party_id = any(${sql.param(roots)}::uuid[])
    `),
  ]);
  const tint = new Map(colors.rows.map((row) => [row.party, row.color]));
  const holdovers = {
    senate: {} as Record<string, number>,
    governor: {} as Record<string, number>,
  };
  for (const row of kept.rows) {
    const side =
      row.office === "senate" ? holdovers.senate : holdovers.governor;
    const key = family(row.party) ?? "";
    side[key] = (side[key] ?? 0) + 1;
  }
  return {
    holders: Object.fromEntries(
      held.rows.map((row) => [
        row.contest,
        { party: family(row.party), name: people.full.get(row.person) ?? "" },
      ]),
    ),
    holdovers,
    families,
    parties: Object.fromEntries(
      roots.map((root) => [
        root,
        {
          name: named.full.get(root) ?? "",
          short: named.short.get(root) ?? named.full.get(root) ?? "",
          color: tint.get(root) ?? null,
        },
      ]),
    ),
  };
}

export type Control = Awaited<ReturnType<typeof control>>;
export type House = Awaited<ReturnType<typeof house>>;
