import { openapi } from "@orpc/openapi";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { names, paths, refs } from "#lib/server/rpc/archive.js";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";

export const seats = {
  gains: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/gains",
        summary:
          "Count the single-member seats each party gained from another since the chamber's previous general election",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          since: v.number(),
          path: v.nullable(v.string()),
          total: v.number(),
          compared: v.number(),
          estimated: v.number(),
          parties: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
            }),
          ),
          flows: v.array(
            v.object({ to: v.string(), from: v.string(), seats: v.number() }),
          ),
          changed: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              to: v.string(),
              from: v.string(),
              isEstimated: v.boolean(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        area: string;
        same: boolean;
        now: string | null;
        before: string | null;
        since: string | null;
        election: string | null;
      }>(sql`
        with recursive tree(id) as (
          select e.id from election e
          join slug ps on ps.entity_id = e.polity_id and ps.is_current and (ps.path = 'gb' or ps.path like 'gb/%')
          where e.id = ${input.id}::uuid
          union all
          select e.id from election e join tree on e.parent_id = tree.id where e.kind not in ('runoff', 'indirect')
        ),
        now as materialized (
          select c.id, c.body_id as body, c.district_area_id as area, e.date_from as day
          from contest c join tree on tree.id = c.election_id join election e on e.id = c.election_id
          where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and coalesce(c.magnitude, 1) = 1 and c.body_id is not null
        ),
        chamber as (
          select body from now group by body order by count(*) desc limit 1
        ),
        last as (
          select e.date_from as day, coalesce(e.parent_id, e.id) as election
          from contest c join election e on e.id = c.election_id
          where c.body_id = (select body from chamber) and e.kind = 'general' and e.status = 'held'
            and e.date_from < (select min(day) from now) - 120
          order by e.date_from desc limit 1
        ),
        prev as materialized (
          select c.id, c.district_area_id as area, e.date_from as day
          from contest c join election e on e.id = c.election_id
          where c.body_id = (select body from chamber) and e.kind = 'general'
            and e.date_from between (select day from last) - 200 and (select day from last)
            and c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and coalesce(c.magnitude, 1) = 1
        ),
        paired as materialized (
          select n.id, n.area, exists (select from prev p where p.area = n.area) as same, coalesce(
            (select p.id from prev p where p.area = n.area limit 1),
            (select p.id from prev p
              join area_geometry gp on gp.area_id = p.area and gp.valid @> p.day
              join area_geometry gn on gn.area_id = n.area and gn.valid @> n.day
              where gp.geom && gn.geom and ST_Intersects(gp.geom, gn.geom)
              order by ST_Area(ST_Intersection(gp.geom, gn.geom, 0.00001)) desc limit 1),
            (select p.id from prev p
              where exists (
                select from name a join name b on lower(a.text) = lower(b.text)
                where a.entity_id = n.area and b.entity_id = p.area
              ) limit 1)
          ) as prior
          from now n where n.body = (select body from chamber)
        ),
        won as (
          select distinct on (rs.contest_id) rs.contest_id as contest,
            coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id)::text as party,
            cy.person_id::text as person
          from result_set rs
          join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          join contest_option o on o.id = x.option_id
          join election e on e.id = rs.election_id
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge pm on pm.entity_id = o.party_id
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
            and br.kind = 'regional_branch' and br.valid @> e.date_from
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          where rs.is_default and rs.contest_id in (select id from paired union select prior from paired)
          order by rs.contest_id, o.k
        )
        select p.id, p.area, p.same,
          coalesce(w.party, 'person:' || w.person) as now,
          coalesce(b.party, 'person:' || b.person) as before,
          (select day::text from last) as since,
          (select election::text from last) as election
        from paired p left join won w on w.contest = p.id left join won b on b.contest = p.prior
      `);
      const since = found.rows[0]?.since;
      const compared = found.rows.filter(
        (row) => row.now != null && row.before != null,
      );
      if (since == null || compared.length === 0) {
        return null;
      }
      const keys = [
        ...new Set(
          compared.flatMap((row) => [row.now ?? "", row.before ?? ""]),
        ),
      ];
      const changed = compared.filter((row) => row.now !== row.before);
      const parties = keys.filter((key) => !key.startsWith("person:"));
      const people = keys.flatMap((key) =>
        key.startsWith("person:") ? [key.slice(7)] : [],
      );
      const [colored, labels, slugs, previous] = await Promise.all([
        refs(parties, since),
        names([...people, ...changed.map((row) => row.area)], since),
        paths(changed.map((row) => row.id)),
        paths([found.rows[0]?.election ?? ""]),
      ]);
      const flows = Map.groupBy(compared, (row) => `${row.now}|${row.before}`)
        .entries()
        .map(([pair, rows]) => {
          const [to = "", from = ""] = pair.split("|");
          return { to, from, seats: rows.length };
        })
        .toArray()
        .toSorted((a, b) => b.seats - a.seats);
      return {
        since: Number(since.slice(0, 4)),
        path: previous.get(found.rows[0]?.election ?? "") ?? null,
        total: found.rows.length,
        compared: compared.length,
        estimated: compared.filter((row) => !row.same).length,
        parties: keys.map((key) => {
          const party = colored.get(key);
          return {
            key,
            label: party?.label ?? labels.full.get(key.slice(7)) ?? "",
            short: party?.short ?? null,
            color: party?.color ?? null,
          };
        }),
        flows,
        changed: changed
          .map((row) => ({
            name: labels.full.get(row.area) ?? "",
            path: slugs.get(row.id) ?? null,
            to: row.now ?? "",
            from: row.before ?? "",
            isEstimated: !row.same,
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
      };
    }),
  change: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/change",
        summary:
          "Say whether a single-member seat's winning party held it or gained it since the chamber's previous general election",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          since: v.number(),
          isGain: v.boolean(),
          basis: v.picklist(["same", "area", "name"]),
          party: v.nullable(
            v.object({ label: v.string(), short: v.nullable(v.string()) }),
          ),
          from: v.nullable(
            v.object({ label: v.string(), short: v.nullable(v.string()) }),
          ),
          prior: v.object({ name: v.string(), path: v.nullable(v.string()) }),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        basis: "same" | "area" | "name";
        prior: string;
        area: string;
        since: string;
        now: string | null;
        before: string | null;
      }>(sql`
        with this as materialized (
          select c.id, c.body_id as body, c.district_area_id as area, e.date_from as day, e.kind
          from contest c join election e on e.id = c.election_id
          join slug ps on ps.entity_id = e.polity_id and ps.is_current and (ps.path = 'gb' or ps.path like 'gb/%')
          where c.id = ${input.id}::uuid and c.kind = 'body_seats' and c.allocation_parent_id is null
            and c.party_scope_id is null and c.district_area_id is not null
            and coalesce(c.magnitude, 1) = 1 and c.body_id is not null
        ),
        last as (
          select e.date_from as day
          from this t join contest c on c.body_id = t.body join election e on e.id = c.election_id
          where e.kind = 'general' and e.status = 'held'
            and e.date_from < t.day - case when t.kind = 'general' then 120 else 0 end
          order by e.date_from desc limit 1
        ),
        prev as materialized (
          select c.id, c.district_area_id as area, e.date_from as day
          from contest c join election e on e.id = c.election_id
          where c.body_id = (select body from this) and e.kind = 'general'
            and e.date_from between (select day from last) - 200 and (select day from last)
            and c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and coalesce(c.magnitude, 1) = 1
        ),
        paired as (
          select t.id, coalesce(s.id, g.id, n.id) as prior, coalesce(s.area, g.area, n.area) as area,
            case when s.id is not null then 'same' when g.id is not null then 'area' else 'name' end as basis
          from this t
          left join lateral (select p.id, p.area from prev p where p.area = t.area limit 1) s on true
          left join lateral (
            select p.id, p.area from prev p
            join area_geometry gp on gp.area_id = p.area and gp.valid @> p.day
            join area_geometry gn on gn.area_id = t.area and gn.valid @> t.day
            where gp.geom && gn.geom and ST_Intersects(gp.geom, gn.geom)
            order by ST_Area(ST_Intersection(gp.geom, gn.geom, 0.00001)) desc limit 1
          ) g on s.id is null
          left join lateral (
            select p.id, p.area from prev p
            where exists (
              select from name a join name b on lower(a.text) = lower(b.text)
              where a.entity_id = t.area and b.entity_id = p.area
            ) limit 1
          ) n on s.id is null and g.id is null
        ),
        won as (
          select distinct on (rs.contest_id) rs.contest_id as contest,
            coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id)::text as party,
            cy.person_id::text as person
          from result_set rs
          join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          join contest_option o on o.id = x.option_id
          join election e on e.id = rs.election_id
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge pm on pm.entity_id = o.party_id
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
            and br.kind = 'regional_branch' and br.valid @> e.date_from
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          where rs.is_default and rs.contest_id in (select id from paired union select prior from paired)
          order by rs.contest_id, o.k
        )
        select p.basis, p.prior, p.area, (select day::text from last) as since,
          coalesce(w.party, 'person:' || w.person) as now,
          coalesce(b.party, 'person:' || b.person) as before
        from paired p left join won w on w.contest = p.id left join won b on b.contest = p.prior
        where p.prior is not null
      `);
      const row = found.rows[0];
      if (row?.now == null || row.before == null) {
        return null;
      }
      const fold = (key: string) =>
        key.startsWith("person:") ? "independent" : key;
      const [colored, labels, slugs] = await Promise.all([
        refs(
          [row.now, row.before].filter((key) => !key.startsWith("person:")),
          row.since,
        ),
        names([row.area], row.since),
        paths([row.prior]),
      ]);
      const party = (key: string) =>
        key.startsWith("person:")
          ? null
          : {
              label: colored.get(key)?.label ?? "",
              short: colored.get(key)?.short ?? null,
            };
      return {
        since: Number(row.since.slice(0, 4)),
        isGain: fold(row.now) !== fold(row.before),
        basis: row.basis,
        party: party(row.now),
        from: party(row.before),
        prior: {
          name: labels.full.get(row.area) ?? "",
          path: slugs.get(row.prior) ?? null,
        },
      };
    }),
};
