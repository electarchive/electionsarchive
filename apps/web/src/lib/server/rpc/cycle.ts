import { os } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Cycle, CycleLine } from "#lib/live/cycle.js";
import { remember } from "#lib/server/cache.js";
import { liveDb as db } from "#lib/server/db/index.js";

type Tally = { party: string; votes: number };

const grouped = sql`
  select coalesce(top.party_id::text, line.party::text, '') as party, sum(c.value)::float8 as votes
  from counted c join contest_option o on o.k = c.option_k
  join contest_option top on top.id = coalesce(o.parent_option_id, o.id)
  left join lateral (
    select x.party_id as party from contest_option x
    where top.party_id is null and x.parent_option_id = top.id and x.party_id is not null
    order by (select sum(y.value) from counted y where y.option_k = x.k) desc nulls last, x.k
    limit 1
  ) line on true
  group by 1
`;

const tally = (set: number, area: number) => sql`
  with counted as materialized (
    select r.option_k, r.value from result_rollup r
    where r.result_set_k = ${set} and r.area_k = ${area} and r.measure = 1 and r.option_k <> 0
      and r.count_round = (
        select min(q.count_round) from result_rollup q
        where q.result_set_k = ${set} and q.area_k = ${area} and q.measure = 1
      )
  )
  ${grouped}
`;

async function families(parties: Array<string>, date: string) {
  const found = await db.execute<{
    id: string;
    family: string;
    color: string | null;
  }>(sql`
    select p.id, coalesce(a.parent_party_id, p.id) as family,
      coalesce(
        (select pc.color from party_color pc where pc.party_id = p.id and pc.valid @> ${date}::date),
        (select pc.color from party_color pc where pc.party_id = a.parent_party_id and pc.valid @> ${date}::date)
      ) as color
    from unnest(${sql.param(parties)}::uuid[]) as p(id)
    left join party_affiliation a on a.party_id = p.id and a.kind = 'regional_branch' and a.valid @> ${date}::date
  `);
  return new Map(found.rows.map((row) => [row.id, row]));
}

async function compute(path: string): Promise<Cycle | null> {
  const { names } = await import("#lib/server/rpc/archive.js");
  const found = await db.execute<{
    area: string | null;
    areaK: number | null;
    body: string | null;
    office: string | null;
    boundarySet: string | null;
    drawn: string;
    priorRoot: string | null;
    priorDay: string | null;
    priorState: string | null;
    stateArea: string | null;
  }>(sql`
    with recursive me as materialized (
      select c.id, c.election_id, c.district_area_id, c.body_id, c.office_id, c.boundary_set_id, e.date_from, e.polity_id,
        coalesce(bs.as_of, e.date_from)::text as drawn
      from slug s join contest c on c.id = s.entity_id join election e on e.id = c.election_id
      left join boundary_set bs on bs.id = c.boundary_set_id
      where s.path = ${path} and s.is_current and c.allocation_parent_id is null and c.party_scope_id is null
      limit 1
    ), up(id, parent, polity, depth) as (
      select e.id, e.parent_id, e.polity_id, 0 from me join election e on e.id = me.election_id
      union all
      select e.id, e.parent_id, e.polity_id, up.depth + 1 from up join election e on e.id = up.parent where up.depth < 6
    )
    select me.district_area_id as area, (select a.k from area a where a.id = me.district_area_id) as "areaK",
      me.body_id as body, me.office_id as office, me.boundary_set_id as "boundarySet", me.drawn,
      pr.id as "priorRoot", pr.date_from::text as "priorDay", ps.id as "priorState",
      (select t.area_id from polity_territory t where t.polity_id = me.polity_id and t.valid @> pr.date_from limit 1) as "stateArea"
    from me
    left join lateral (
      select x.id, x.date_from from election x
      where x.polity_id = (select polity from up where parent is null limit 1)
        and x.parent_id is null and x.kind = 'general' and x.date_from < me.date_from
      order by x.date_from desc limit 1
    ) pr on true
    left join lateral (
      select x.id from election x
      where x.parent_id = pr.id and x.polity_id = me.polity_id and x.kind = 'general'
      limit 1
    ) ps on true
  `);
  const me = found.rows[0];
  if (
    me?.area == null ||
    me.areaK == null ||
    me.priorState == null ||
    me.priorRoot == null ||
    me.priorDay == null ||
    (me.body == null && me.office == null)
  ) {
    return null;
  }
  const { area, areaK, priorDay, priorState, body, drawn } = me;
  const seat = me.office;
  const contests = await db.execute<{
    id: string;
    kind: "national" | "office";
    area: string;
    areaK: number;
    boundarySet: string | null;
    label: string | null;
    path: string | null;
  }>(sql`
    with roots as materialized (
      select r.id from contest r where r.election_id = ${me.priorRoot}::uuid and r.allocation_parent_id is null and not r.notional
    ), states as materialized (
      select c.id, c.district_area_id from contest c
      where c.election_id = ${priorState}::uuid and c.allocation_parent_id = any(array(select id from roots)) and not c.notional
        and c.district_area_id = any(array[${area}::uuid, ${me.stateArea}::uuid])
    ), picked as (
      select c.id, 'office' as kind, c.district_area_id, c.boundary_set_id, coalesce(c.body_id, c.office_id) as label
      from contest c
      where c.election_id = ${priorState}::uuid and c.district_area_id = any(array[${area}::uuid])
        and (c.body_id = ${body}::uuid or (c.body_id is null and c.office_id = ${seat}::uuid))
        and c.allocation_parent_id is null and c.party_scope_id is null and c.previous_round_id is null and not c.notional
      union all
      select c.id, 'national', c.district_area_id, c.boundary_set_id, (select r.office_id from contest r where r.id = c.allocation_parent_id)
      from contest c
      where c.id in (select id from states)
        or (c.allocation_parent_id in (select id from states) and c.district_area_id = any(array[${area}::uuid]) and not c.notional)
    )
    select p.id, p.kind, p.district_area_id as area, a.k as "areaK", p.boundary_set_id as "boundarySet", p.label,
      (select s.path from slug s where s.entity_id = p.id and s.is_current limit 1) as path
    from picked p join area a on a.id = p.district_area_id
    where exists (
      select from result_set rs where rs.contest_id = p.id and rs.status = 'active' and rs.kind = 'counted' and rs.stage in ('official', 'certified')
    )
  `);
  const office = contests.rows.find((row) => row.kind === "office") ?? null;
  const nationals = contests.rows.filter((row) => row.kind === "national");
  const national =
    nationals.find((row) => row.area === area) ??
    nationals.find((row) => row.area === me.stateArea) ??
    null;
  const chosen = [office, national].flatMap((row) =>
    row == null ? [] : [row],
  );
  if (chosen.length === 0) {
    return null;
  }
  const sets = await db.execute<{ contest: string; k: number }>(sql`
    select p.id as contest, x.k from unnest(${sql.param(chosen.map((row) => row.id))}::uuid[]) as p(id)
    cross join lateral (
      select rs.k from result_set rs
      where rs.contest_id = p.id and rs.status = 'active' and rs.kind = 'counted' and rs.stage in ('official', 'certified')
      order by rs.is_default desc, rs.stage = 'certified' desc, rs.k desc
      limit 1 offset 0
    ) x
  `);
  const setOf = new Map(sets.rows.map((row) => [row.contest, row.k]));
  const redrawn =
    office != null &&
    me.boundarySet != null &&
    office.boundarySet != null &&
    office.boundarySet !== me.boundarySet;

  async function exact(contest: string, at: number) {
    const set = setOf.get(contest);
    if (set == null) {
      return [];
    }
    const rows = await db.execute<{ party: string; votes: number }>(
      tally(set, at),
    );
    return rows.rows;
  }

  async function apportioned(contest: string, whole: number) {
    const [found, state] = await Promise.all([
      db.execute<Tally & { rough: number | null; placed: number | null }>(sql`
        with ranked as materialized (
          select rs.k from result_set rs
          where rs.contest_id = ${contest}::uuid and rs.status = 'active' and rs.kind = 'counted' and rs.stage in ('official', 'certified')
          order by rs.is_default desc, rs.stage = 'certified' desc, rs.k desc
        ), picked as materialized (
          select r.k from ranked r
          where exists (
            select from result_value v join reporting_unit u on u.k = v.unit_k
            where v.result_set_k = r.k and v.measure = 1 and u.scheme_code = 'us.precinct'
          )
          limit 1
        ), shape as materialized (
          select g.geom from area_geometry g where g.area_id = ${area}::uuid and g.valid @> ${drawn}::date limit 1
        ), votes as materialized (
          select v.unit_k, v.option_k, v.value from result_value v
          where v.result_set_k = (select k from picked) and v.measure = 1 and v.option_k <> 0
            and v.count_round = (select min(q.count_round) from result_value q where q.result_set_k = (select k from picked) and q.measure = 1)
        ), blocks as materialized (
          select u.area_id, array_agg(u.k) as units, bool_and(r.scheme_code = u.scheme_code) as precise
          from (select distinct unit_k from votes) x
          join reporting_unit u on u.k = x.unit_k
          join area r on r.id = u.area_id
          group by 1
        ), located as materialized (
          select b.area_id, b.units, b.precise, g.point from blocks b
          cross join lateral (
            select coalesce(a.label_point, ST_PointOnSurface(a.geom)) as point from area_geometry a
            where a.area_id = b.area_id and a.valid @> ${priorDay}::date
            limit 1
          ) g
        ), near as materialized (
          select l.units, l.precise, 1 as share from located l cross join shape s
          where l.precise and l.point && s.geom and ST_Intersects(s.geom, l.point)
          union all
          select l.units, l.precise, ST_Area(ST_Intersection(s.geom, a.geom)) / nullif(ST_Area(a.geom), 0)
          from located l cross join shape s
          join area_geometry a on a.area_id = l.area_id and a.valid @> ${priorDay}::date
          where not l.precise and a.geom && s.geom
        ), weights as materialized (
          select unnest(units) as unit_k, case when share > 0.98 then 1 else share end as share,
            not precise and share <= 0.98 as rough
          from near where share > 0.02
        ), counted as materialized (
          select v.option_k, sum(v.value * w.share) as value from votes v join weights w on w.unit_k = v.unit_k group by 1
        ), parties as (${grouped})
        select p.party, p.votes,
          (select sum(v.value * w.share) from votes v join weights w on w.unit_k = v.unit_k where w.rough)::float8 as rough,
          (select sum(v.value) from votes v join (select unnest(units) as unit_k from located) l on l.unit_k = v.unit_k)::float8 as placed
        from parties p
      `),
      exact(contest, whole),
    ]);
    let total = 0;
    for (const row of state) {
      total += row.votes;
    }
    const first = found.rows[0];
    let inside = 0;
    for (const row of found.rows) {
      inside += row.votes;
    }
    const placed = first?.placed ?? 0;
    if (
      first == null ||
      inside <= 0 ||
      (first.rough ?? 0) > inside * 0.1 ||
      placed < total * 0.97 ||
      placed > total * 1.03
    ) {
      return null;
    }
    return inside < total * 0.995
      ? { rows: found.rows, isEstimated: true }
      : { rows: state, isEstimated: false };
  }

  const [officeRows, nationalRows] = await Promise.all([
    office == null ? null : exact(office.id, office.areaK),
    national == null
      ? null
      : national.area === area
        ? exact(national.id, areaK).then((rows) => ({
            rows,
            isEstimated: false,
          }))
        : apportioned(national.id, national.areaK),
  ]);
  const parties = [
    ...new Set(
      [...(officeRows ?? []), ...(nationalRows?.rows ?? [])].flatMap((row) =>
        row.party === "" ? [] : [row.party],
      ),
    ),
  ];
  const labels = chosen.flatMap((row) =>
    row.label == null ? [] : [row.label],
  );
  const [named, family] = await Promise.all([
    names([...parties, ...labels], priorDay),
    families(parties, priorDay),
  ]);

  function line(
    kind: "national" | "office",
    contest: (typeof chosen)[number],
    rows: Array<Tally>,
    isEstimated: boolean,
  ): CycleLine | null {
    let total = 0;
    for (const row of rows) {
      total += row.votes;
    }
    if (total <= 0) {
      return null;
    }
    const top = rows
      .filter((row) => row.party !== "")
      .toSorted((a, b) => b.votes - a.votes)
      .slice(0, 3)
      .map((row) => {
        const found = family.get(row.party);
        return {
          id: row.party,
          family: found?.family ?? row.party,
          name: named.full.get(row.party) ?? "",
          short: named.short.get(row.party) ?? named.full.get(row.party) ?? "",
          color: found?.color ?? null,
          share: Math.round((row.votes / total) * 1_000_000) / 1_000_000,
        };
      });
    if (top.length < 2) {
      return null;
    }
    return {
      kind,
      label:
        (contest.label == null
          ? null
          : (named.short.get(contest.label) ??
            named.full.get(contest.label))) ?? "",
      slug: contest.path,
      isEstimated,
      isRedrawn: kind === "office" && redrawn,
      parties: top,
    };
  }

  const lines = [
    national == null || nationalRows == null
      ? null
      : line("national", national, nationalRows.rows, nationalRows.isEstimated),
    office == null || officeRows == null
      ? null
      : line("office", office, officeRows, false),
  ].flatMap((entry) => (entry == null ? [] : [entry]));
  return lines.length === 0 ? null : { date: priorDay, lines };
}

export async function priorCycle(path: string) {
  return remember(`live:cycle:v1:${path}`, async () => compute(path));
}

export const cycle = os
  .input(
    v.object({
      path: v.pipe(
        v.string(),
        v.regex(/^[a-z]+(?:\/[a-z0-9-]+){2,4}$/u),
        v.maxLength(200),
      ),
    }),
  )
  .handler(async ({ input }) => priorCycle(input.path));
