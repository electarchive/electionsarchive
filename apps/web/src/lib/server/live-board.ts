import { sql } from "drizzle-orm";
import type { Geography, Pieces } from "#lib/live/standing.js";
import { liveDb as db } from "#lib/server/db/index.js";
import america from "#lib/server/hex/us-cd.json";
import { plane, spot } from "#lib/server/plane.js";
import { names } from "#lib/server/rpc/archive.js";

export async function framing(
  scheme: string,
  ids: Array<string>,
  date: string,
) {
  const box = await db.execute<{
    tiles: string;
    west: number | null;
    south: number | null;
    east: number | null;
    north: number | null;
  }>(sql`
    select coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles,
      st_xmin(x.box) as west, st_ymin(x.box) as south, st_xmax(x.box) as east, st_ymax(x.box) as north
    from boundary_set bs, lateral (
      select ST_Transform(ST_SetSRID(ST_Extent(${plane("g.geom", "a.scheme_code")})::geometry, 3857), 4326) as box
      from area_geometry g join area a on a.id = g.area_id where g.area_id = any(${sql.param(ids)}::uuid[]) and g.valid @> ${date}::date
    ) x
    where bs.scheme_code = ${scheme} and (bs.valid @> ${date}::date or bs.valid_from > ${date}::date)
    order by (
        select count(*) from area_geometry g
        where g.area_id = any(${sql.param(ids)}::uuid[]) and g.valid @> ${date}::date and g.valid @> bs.as_of
      ) desc,
      abs(bs.as_of - ${date}::date)
    limit 1
  `);
  return box.rows[0];
}

export async function geography(
  areaIds: Array<string>,
  date: string,
  framed: Array<string> = areaIds,
  isOutlined = true,
  isGrounded = false,
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
  const extent = async (ids: Array<string>) => framing(scheme, ids, date);
  const ground = isGrounded
    ? (
        await db.execute<{ id: string; k: number }>(sql`
          select distinct a.id, a.k from area a
          join area_code c on c.area_id = a.id and c.valid @> ${date}::date
          where a.scheme_code = ${scheme}
            and split_part(c.code, ':', 1) in (
              select split_part(o.code, ':', 1) from area_code o
              where o.area_id = any(${sql.param(areaIds)}::uuid[]) and o.valid @> ${date}::date and position(':' in o.code) > 0
            )
            and exists (select 1 from area_geometry g where g.area_id = a.id and g.valid @> ${date}::date)
        `)
      ).rows
    : [];
  let region = ground.length > 0 ? ground.map((area) => area.id) : framed;
  let row = await extent(region);
  if (row?.west == null && framed !== areaIds) {
    region = areaIds;
    row = await extent(region);
  }
  if (
    row?.west == null ||
    row.south == null ||
    row.east == null ||
    row.north == null
  ) {
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
          select ST_Union(ST_MakeValid(ST_SimplifyPreserveTopology(${plane("g.geom", "a.scheme_code")}, 50))) as geom
          from area_geometry g join area a on a.id = g.area_id
          where g.area_id = any(${sql.param(region)}::uuid[]) and g.valid @> ${date}::date
        ) p
      ) q
    `)
    : null;
  return {
    tiles: row.tiles,
    scheme: scheme.split(".", 1)[0] ?? scheme,
    outline: drawn?.rows[0]?.rings ?? [],
    isEdged: region.some((id) => !areaIds.includes(id)),
    ground: ground.map((area) => area.k),
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

export async function pieces(
  clip: string,
  date: string,
  scheme: string,
): Promise<Pieces | null> {
  const found = await db.execute<{
    pieces: Pieces["pieces"] | null;
    edge: Pieces["edge"] | null;
    veil: Pieces["veil"] | null;
  }>(sql`
    with edge as materialized (
      select ST_MakeValid(g.geom) as geom from area a
      join area_geometry g on g.area_id = a.id and g.valid @> ${date}::date
      where a.id = ${clip}::uuid and a.scheme_code not in ('us.state', 'us.county')
      order by g.valid_from desc nulls last limit 1
    ),
    picked as materialized (
      select distinct on (a.id) a.id, a.k, a.scheme_code as scheme, g.geom
      from edge e join area_geometry g on g.geom && e.geom and g.valid @> ${date}::date
      join area a on a.id = g.area_id and a.scheme_code = ${scheme}
      order by a.id, g.valid_from desc nulls last
    ),
    cut as materialized (
      select p.id, p.k, p.scheme, ST_Area(p.geom) as whole, ST_Area(e.geom) as clip,
        ST_CollectionExtract(ST_Intersection(ST_MakeValid(ST_ClipByBox2D(p.geom, e.geom)), e.geom), 3) as geom
      from picked p cross join edge e
    ),
    planed as materialized (
      select id, k, ${plane("geom", "scheme")} as geom from cut
      where not ST_IsEmpty(geom) and ST_Area(geom) >= 0.004 * least(whole, clip)
    ),
    thin as materialized (
      select id, k, ST_CollectionExtract(ST_ReducePrecision(ST_MakeValid(
        ST_Transform(ST_SetSRID(ST_CoverageSimplify(geom, t) over (), 3857), 4326)
      ), 0.00001), 3) as geom
      from (select id, k, geom, sqrt(sum(ST_Area(geom)) over ()) / 1500 as t from planed) q
    ),
    whole as materialized (
      select ST_UnaryUnion(ST_Collect(ST_MakeValid(geom))) as geom from thin
    )
    select
      (select json_agg(json_build_object(
        'areaId', id, 'k', k,
        'at', json_build_array(round(ST_X(p)::numeric, 5), round(ST_Y(p)::numeric, 5)),
        'shape', ST_AsGeoJSON(ST_Multi(geom), 5)::json -> 'coordinates'
      )) from thin cross join lateral (select ST_PointOnSurface(geom) as p) s where not ST_IsEmpty(geom)) as pieces,
      (select ST_AsGeoJSON(ST_Multi(ST_Boundary(geom)), 5)::json -> 'coordinates' from whole) as edge,
      (select ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(ST_Difference(ST_MakeEnvelope(-180, -85, 180, 85, 4326), geom), 3)), 5)::json -> 'coordinates' from whole) as veil
  `);
  const row = found.rows[0];
  return row?.pieces == null || row.pieces.length === 0
    ? null
    : { pieces: row.pieces, edge: row.edge ?? [], veil: row.veil ?? [] };
}

export async function boxes(areaIds: Array<string>, date: string) {
  if (areaIds.length === 0) {
    return {};
  }
  const found = await db.execute<{
    id: string;
    west: number | null;
    south: number | null;
    east: number | null;
    north: number | null;
  }>(sql`
    select x.id, st_xmin(x.box) as west, st_ymin(x.box) as south, st_xmax(x.box) as east, st_ymax(x.box) as north
    from (
      select a.id, ST_Transform(ST_SetSRID(ST_Extent(${plane("g.geom", "a.scheme_code")})::geometry, 3857), 4326) as box
      from area_geometry g join area a on a.id = g.area_id
      where g.area_id = any(${sql.param(areaIds)}::uuid[]) and g.valid @> ${date}::date
      group by a.id
    ) x
  `);
  return Object.fromEntries(
    found.rows.flatMap((row) =>
      row.west == null ||
      row.south == null ||
      row.east == null ||
      row.north == null
        ? []
        : [
            [
              row.id,
              {
                west: row.west,
                south: row.south,
                east: row.east,
                north: row.north,
              },
            ],
          ],
    ),
  );
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

export async function hexes(date: string) {
  const load =
    LAYOUTS[`./hex/us-cd-${america[date as keyof typeof america] ?? ""}.json`];
  return load == null ? null : load();
}

export async function shapes(
  areaIds: Array<string>,
  date: string,
  clip: string | null,
) {
  if (areaIds.length === 0) {
    return null;
  }
  const found = await db.execute<{
    id: string;
    d: string;
    width: number;
    height: number;
  }>(sql`
    with picked as materialized (
      select distinct on (a.id) a.id, a.scheme_code as scheme, g.geom
      from area a join area_geometry g on g.area_id = a.id and g.valid @> ${date}::date
      where a.id = any(${sql.param(areaIds)}::uuid[])
      order by a.id, g.valid_from desc nulls last
    ),
    edge as materialized (
      select g.geom from area_geometry g
      where g.area_id = ${clip}::uuid and g.valid @> ${date}::date
      order by g.valid_from desc nulls last limit 1
    ),
    cut as materialized (
      select p.id, p.scheme, coalesce(ST_CollectionExtract(ST_Intersection(ST_MakeValid(p.geom), ST_MakeValid(e.geom)), 3), p.geom) as geom
      from picked p left join edge e on true
    ),
    planed as materialized (
      select id, ${plane("geom", "scheme")} as geom from cut where not ST_IsEmpty(geom)
    ),
    framed as materialized (
      select id, geom, west, north, east - west as wide, north - south as tall, greatest(east - west, north - south) as side
      from (
        select id, geom, min(ST_XMin(geom)) over () as west, max(ST_XMax(geom)) over () as east,
          min(ST_YMin(geom)) over () as south, max(ST_YMax(geom)) over () as north
        from planed where geom is not null and not ST_IsEmpty(geom)
      ) f
    ),
    thin as (
      select id, wide * 1000 / side as width, tall * 1000 / side as height,
        ST_SnapToGrid(ST_Scale(ST_Translate(
          ST_CoverageSimplify(geom, side / 400) over (),
        -west, -north), 1000 / side, 1000 / side), 1) as geom
      from framed
    )
    select id, ST_AsSVG(geom, 1, 0) as d, width, height from thin where not ST_IsEmpty(geom)
  `);
  const first = found.rows[0];
  return first == null
    ? null
    : {
        width: Math.ceil(first.width),
        height: Math.ceil(first.height),
        paths: found.rows.map((row) => ({ areaId: row.id, d: row.d })),
      };
}

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
  const [map, layout] = await Promise.all([
    geography(ids, date, ids, false),
    hexes(date),
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
  const [held, kept, head] = await Promise.all([
    db.execute<{
      contest: string;
      person: string;
      vacant: boolean;
      party: string | null;
      grouped: boolean;
      caucus: string | null;
    }>(sql`
      with up as materialized (
        select c.id, c.seat_id, c.office_id, c.body_id, c.district_area_id from contest c where c.id = any(${sql.param(ids)}::uuid[])
      ),
      sitting as materialized (
        select t.id, t.person_id, t.contest_id, t.seat_id, t.office_id, t.body_id, t.from_date, t.to_date, s.district_area_id
        from tenure t left join seat s on s.id = t.seat_id
        where t.from_date <= ${date}::date and (t.to_date is null or t.to_date > ${date}::date - 730)
      ),
      held as (
        select distinct on (m.contest) m.contest, m.tenure, m.person, m.won, m.to_date <= ${date}::date as vacant from (
          select u.id as contest, t.id as tenure, t.person_id as person, t.contest_id as won, t.from_date, t.to_date
          from up u join sitting t on t.seat_id = u.seat_id
          union all
          select u.id, t.id, t.person_id, t.contest_id, t.from_date, t.to_date
          from up u join sitting t on t.office_id = u.office_id
          union all
          select u.id, t.id, t.person_id, t.contest_id, t.from_date, t.to_date
          from up u cross join lateral (
            select u.district_area_id as area_id
            union
            select x.area_id from area_code y join area_code x on x.code = y.code where y.area_id = u.district_area_id
          ) a
          join sitting t on t.body_id = u.body_id
          left join contest w on w.id = t.contest_id
          where u.seat_id is null and u.office_id is null
            and (t.district_area_id = a.area_id or (w.body_id = u.body_id and w.district_area_id = a.area_id))
        ) m
        order by m.contest, m.to_date is null or m.to_date > ${date}::date desc, m.from_date desc
      )
      select h.contest, h.person, h.vacant,
        case when pa.found then pa.party_id else (
          select o.party_id from candidacy ca join contest_option o on o.id = ca.option_id where ca.person_id = h.person and o.party_id is not null
          order by (o.contest_id = h.won) desc nulls last, o.contest_id desc limit 1
        ) end as party,
        coalesce(gr.found, false) as grouped, gr.party_id as caucus
      from held h
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = h.tenure and ta.kind = 'party' and ta.valid @> ${date}::date order by ta.party_id is null limit 1
      ) pa on true
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = h.tenure and ta.kind = 'parliamentary_group' and ta.valid @> ${date}::date limit 1
      ) gr on true
    `),
    db.execute<{
      office: string;
      person: string;
      party: string | null;
      grouped: boolean;
      caucus: string | null;
    }>(sql`
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
      select s.office, s.person_id as person,
        case when pa.found then pa.party_id else (
          select o.party_id from candidacy ca join contest_option o on o.id = ca.option_id where ca.person_id = s.person_id and o.party_id is not null
          order by (o.contest_id = s.contest_id) desc nulls last, o.contest_id desc limit 1
        ) end as party,
        coalesce(gr.found, false) as grouped, gr.party_id as caucus
      from sitting s
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = s.id and ta.kind = 'party' and ta.valid @> ${date}::date order by ta.party_id is null limit 1
      ) pa on true
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = s.id and ta.kind = 'parliamentary_group' and ta.valid @> ${date}::date limit 1
      ) gr on true
    `),
    db.execute<{ party: string | null }>(sql`
      select case when pa.found then pa.party_id else (
        select o.party_id from candidacy ca join contest_option o on o.id = ca.option_id where ca.person_id = t.person_id and o.party_id is not null
        order by (o.contest_id = t.contest_id) desc nulls last, o.contest_id desc limit 1
      ) end as party
      from tenure t
      join office o on o.id = t.office_id
      join polity p on p.id = o.polity_id
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = t.id and ta.kind = 'party' and ta.valid @> ${date}::date order by ta.party_id is null limit 1
      ) pa on true
      where o.kind = 'head_of_state' and p.iso_code = 'US'
        and t.from_date <= ${date}::date and (t.to_date is null or t.to_date > ${date}::date)
      order by t.from_date desc limit 1
    `),
  ]);
  const parties = [
    ...new Set(
      [
        ...[...held.rows, ...kept.rows].flatMap((row) => [
          row.party,
          row.caucus,
        ]),
        ...head.rows.map((row) => row.party),
        ...races.flatMap((race) =>
          race.options.map((option) => option.party?.id ?? null),
        ),
      ].filter((id) => id != null),
    ),
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
  const sitter = (row: {
    person: string;
    party: string | null;
    grouped: boolean;
    caucus: string | null;
  }) => {
    const member = family(row.party);
    return {
      member,
      caucus: row.grouped ? family(row.caucus) : member,
      name: people.full.get(row.person) ?? "",
    };
  };
  const holdovers: Record<"senate" | "governor", Array<Holdover>> = {
    senate: [],
    governor: [],
  };
  const caucuses: Record<string, string> = {};
  for (const row of [...held.rows, ...kept.rows]) {
    const { member, caucus } = sitter(row);
    if (caucus != null && caucus !== member) {
      caucuses[row.person] = caucus;
    }
  }
  for (const row of kept.rows) {
    const { member, caucus, name } = sitter(row);
    const list =
      row.office === "senate" ? holdovers.senate : holdovers.governor;
    const found = list.find(
      (entry) =>
        entry.caucus === (caucus ?? "") && entry.party === (member ?? ""),
    );
    const entry = found ?? {
      caucus: caucus ?? "",
      party: member ?? "",
      count: 0,
      names: [],
    };
    entry.count += 1;
    if (caucus !== member) {
      entry.names.push(name);
    }
    if (found == null) {
      list.push(entry);
    }
  }
  return {
    holders: Object.fromEntries(
      held.rows.map((row) => {
        const { member, caucus, name } = sitter(row);
        return [
          row.contest,
          {
            party: caucus,
            member,
            person: row.person,
            name,
            isVacant: row.vacant,
          },
        ];
      }),
    ),
    holdovers,
    caucuses,
    tiebreak: { senate: family(head.rows[0]?.party ?? null) },
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

type Holdover = {
  caucus: string;
  party: string;
  count: number;
  names: Array<string>;
};

export type Control = Awaited<ReturnType<typeof control>>;
export type House = Awaited<ReturnType<typeof house>>;

export async function portraits(people: Array<string>) {
  if (people.length === 0) {
    return new Map<string, string>();
  }
  const found = await db.execute<{ person: string; path: string }>(sql`
    select distinct on (person_id) person_id as person, path
    from person_image where person_id = any(${sql.param([...new Set(people)])}::uuid[])
    order by person_id, recorded_from desc
  `);
  return new Map(found.rows.map((row) => [row.person, `/${row.path}`]));
}
