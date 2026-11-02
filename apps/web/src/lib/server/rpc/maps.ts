import type { SQL } from "drizzle-orm";
import { openapi } from "@orpc/openapi";
import { call, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { boxes, vintage, zooms } from "#lib/server/plane.js";
import {
  contests,
  names,
  paths,
  polities,
  races,
  served,
} from "#lib/server/rpc/archive.js";

const Tiles = v.pipe(
  v.string(),
  v.regex(/^[a-z]{2,5}\.[a-z0-9_]+\/[\w:.-]+$/u),
);

const Id = v.pipe(v.string(), v.uuid(), v.toLowerCase());

const Stop = v.object({
  name: v.string(),
  date: v.nullable(v.string()),
  path: v.string(),
});

const mapped = (contest: SQL) => sql`
  (
    exists (
      select from contest c
      join result_set rs on rs.contest_id = c.id and rs.status = 'active' and ${served("rs")}
      where c.allocation_parent_id = ${contest}
    )
    or exists (
      select from contest c
      join result_set rs on rs.contest_id = c.id and rs.status = 'active' and ${served("rs")}
      left join area da on da.id = c.district_area_id
      where c.id = ${contest}
        and case
          when da.id is null then (
            select count(distinct z.area_k) from (select x.area_k from result_rollup x where x.result_set_k = rs.k limit 500) z
          ) > 1
          else exists (
            select from area_parent ap join area a on a.id = ap.area_id
            where ap.parent_id = da.id
              and exists (select from result_rollup x where x.result_set_k = rs.k and x.area_k = a.k)
          )
        end
    )
  )
`;

const Boxes = v.array(
  v.object({
    name: v.string(),
    width: v.number(),
    height: v.number(),
    units: v.array(v.object({ k: v.number(), d: v.string() })),
  }),
);

export const maps = {
  insets: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/{scheme}/insets",
        summary:
          "Draw the places a national map sets apart in fixed boxes, such as Alaska and Hawaii on a map of the United States: each box's name and size, and a simplified SVG path for every place in it, drawn from the box's top-left corner",
      }),
    )
    .input(
      v.object({
        scheme: v.pipe(v.string(), v.regex(/^[a-z]{2,5}\.[a-z0-9_]+$/u)),
        day: v.pipe(v.string(), v.isoDate()),
        ks: v.pipe(v.array(v.pipe(v.number(), v.integer())), v.maxLength(5000)),
      }),
    )
    .output(Boxes)
    .handler(async ({ input }) => boxes([input])),
  detached: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/detached",
        summary:
          "Draw the overseas places a national map lists apart, such as the constituencies of Guadeloupe and Guyane on a map of France, grouped by territory: each territory's name and size, and a simplified SVG path for every place in it, drawn from the territory's top-left corner",
      }),
    )
    .input(
      v.object({
        tiles: Tiles,
        ks: v.pipe(v.array(v.pipe(v.number(), v.integer())), v.maxLength(500)),
      }),
    )
    .output(Boxes)
    .handler(async ({ input }) => {
      const held = await vintage(input.tiles);
      return held == null
        ? []
        : boxes([{ scheme: held.scheme, day: held.day, ks: input.ks }]);
    }),
  apart: os
    .meta(
      openapi({
        method: "POST",
        path: "/maps/apart",
        summary:
          "Draw the places a national map sets apart, such as the counties and precincts of Alaska and Hawaii on a map of the United States, from up to four tile sets at once: each box's name and size, and a simplified SVG path for every place in it, drawn from the box's top-left corner",
      }),
    )
    .input(
      v.object({
        sets: v.pipe(
          v.array(
            v.object({
              tiles: Tiles,
              ks: v.pipe(
                v.array(v.pipe(v.number(), v.integer())),
                v.maxLength(2500),
              ),
            }),
          ),
          v.maxLength(4),
        ),
      }),
    )
    .output(Boxes)
    .handler(async ({ input }) => {
      const asks = await Promise.all(
        input.sets.map(async (set) => {
          const held = await vintage(set.tiles);
          return held == null
            ? []
            : [{ scheme: held.scheme, day: held.day, ks: set.ks }];
        }),
      );
      return boxes(asks.flat());
    }),
  zooms: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/zooms",
        summary:
          "Find the dense clusters of small places on a map, such as Paris and its inner suburbs on a map of French constituencies, and draw each one at its own scale: its name, its frame in map and geographic coordinates, the places at its core, the cluster it sits inside, and a simplified SVG path for every place in its frame, drawn from the frame's top-left corner with its longer side 600 units long",
      }),
    )
    .input(
      v.object({
        tiles: Tiles,
        ks: v.pipe(v.array(v.pipe(v.number(), v.integer())), v.maxLength(5000)),
      }),
    )
    .output(
      v.array(
        v.object({
          name: v.string(),
          kind: v.picklist(["exact", "around", "inside"]),
          within: v.nullable(v.number()),
          core: v.array(v.number()),
          frame: v.object({
            west: v.number(),
            south: v.number(),
            east: v.number(),
            north: v.number(),
          }),
          x: v.number(),
          y: v.number(),
          width: v.number(),
          height: v.number(),
          units: v.array(v.object({ k: v.number(), d: v.string() })),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const { day, ids, zones } = await zooms(input.tiles, input.ks);
      const labels = await names(
        zones.flatMap((zone) => (zone.place == null ? [] : [zone.place])),
        day,
      );
      const isUnnamed = zones.some((zone) => zone.kind === "inside");
      const units = isUnnamed ? await names(ids, day) : null;
      const words = (id: string) =>
        new Set(units?.full.get(id)?.match(/\p{Lu}[\p{L}'’]{3,}/gu));
      const counted = new Map<string, number>();
      for (const id of isUnnamed ? ids : []) {
        for (const word of words(id)) {
          counted.set(word, (counted.get(word) ?? 0) + 1);
        }
      }
      const common = (cores: Array<string>) => {
        const inside = new Map<string, number>();
        for (const id of cores) {
          for (const word of words(id)) {
            inside.set(word, (inside.get(word) ?? 0) + 1);
          }
        }
        const [word] =
          inside
            .entries()
            .filter(
              ([word, count]) =>
                count >= Math.max(3, cores.length / 4) &&
                count >= (counted.get(word) ?? 0) * 0.75,
            )
            .toArray()
            .toSorted((a, b) => b[1] - a[1])[0] ?? [];
        return word ?? "";
      };
      return zones.map(({ place, cores, kind, ...zone }) => {
        const word = kind === "inside" ? common(cores) : "";
        return {
          ...zone,
          kind: word === "" ? kind : ("exact" as const),
          name:
            word === ""
              ? place == null
                ? ""
                : (labels.full.get(place) ?? "")
              : word,
        };
      });
    }),
  nav: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/nav/{id}",
        summary:
          "Find where a contest's map can go next: the other races with a map in the same election, the same race in every election of the polity, the polity's other elections, and the latest national map of every country",
      }),
    )
    .input(
      v.object({
        id: Id,
        whole: v.optional(Id),
        anchor: v.optional(v.pipe(v.string(), v.regex(/^[a-z0-9-]+$/u))),
      }),
    )
    .output(
      v.object({
        polity: v.object({ name: v.string(), path: v.nullable(v.string()) }),
        election: v.object({
          name: v.string(),
          date: v.nullable(v.string()),
          path: v.nullable(v.string()),
        }),
        races: v.array(v.object({ name: v.string(), path: v.string() })),
        series: v.array(
          v.object({
            ...Stop.entries,
            color: v.nullable(v.string()),
            winner: v.nullable(v.string()),
          }),
        ),
        elections: v.array(Stop),
        polities: v.array(v.object({ name: v.string(), path: v.string() })),
      }),
    )
    .handler(async ({ input }) => {
      const whole = input.anchor == null ? null : (input.whole ?? null);
      const held = await db.execute<{
        id: string;
        polity: string;
        date: string | null;
      }>(sql`
        select e.id, e.polity_id as polity, e.date_from::text as date from election e
        where e.id = coalesce(${whole}::uuid, (select election_id from contest where id = ${input.id}::uuid))
      `);
      const election = held.rows[0];
      if (election == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const [lineup, timeline, tops, countries] = await Promise.all([
        races(election.id, election.polity, election.date, true),
        whole == null
          ? call(contests.get, { id: input.id })
              .then(async (page) => {
                const listed = page.series.flatMap((entry) =>
                  entry.path == null ? [] : [entry.path],
                );
                const shown = await db.execute<{ path: string }>(sql`
                  select s.path from slug s
                  where s.path = any(${sql.param(listed)}::text[]) and s.is_current
                    and ${mapped(sql`s.entity_id`)}
                `);
                const kept = new Set(shown.rows.map((row) => row.path));
                return page.series.flatMap((entry) =>
                  entry.path == null ||
                  entry.annulled ||
                  entry.notional ||
                  !kept.has(entry.path)
                    ? []
                    : [
                        {
                          name: entry.name,
                          date: entry.date,
                          path: entry.path,
                          color: entry.winner?.color ?? null,
                          winner:
                            entry.winner?.short ?? entry.winner?.label ?? null,
                        },
                      ],
                );
              })
              .catch(() => [])
          : db
              .execute<{ id: string; date: string }>(
                sql`
                  with recursive tops(top, id, date) as (
                    select e.id, e.id, e.date_from from election e
                    where e.polity_id = ${election.polity}::uuid
                      and e.parent_id is not distinct from (select parent_id from election where id = ${whole}::uuid)
                      and e.status not in ('annulled', 'canceled')
                    union all
                    select tops.top, k.id, tops.date from election k join tops on k.parent_id = tops.id
                  )
                  select tops.top as id, tops.date::text as date
                  from tops
                  join contest c on c.election_id = tops.id
                  join contest me on me.id = ${input.id}::uuid
                  where coalesce(c.body_id, c.office_id) = coalesce(me.body_id, me.office_id) and c.kind = me.kind
                    and c.allocation_parent_id is null and not c.notional and c.district_area_id is not null
                  group by tops.top, tops.date
                  having count(*) >= 3
                  order by tops.date
                `,
              )
              .then(async (found) => {
                const ids = found.rows.map((row) => row.id);
                const [labels, slugs] = await Promise.all([
                  names(ids),
                  paths(ids),
                ]);
                return found.rows.flatMap((row) => {
                  const at = slugs.get(row.id);
                  return at == null
                    ? []
                    : [
                        {
                          name: labels.full.get(row.id) ?? "",
                          date: row.date,
                          path: `${at}/${input.anchor ?? ""}`,
                          color: null,
                          winner: null,
                        },
                      ];
                });
              }),
        db.execute<{ id: string; date: string }>(sql`
          select e.id, e.date_from::text as date from election e
          where e.polity_id = ${election.polity}::uuid and e.status = 'held' and e.date_from <= current_date
            and not exists (select from election x where x.id = e.parent_id and x.polity_id = e.polity_id)
            and not exists (select from entity_merge m where m.entity_id = e.id)
          order by e.date_from desc
          limit 200
        `),
        call(polities.list, {
          kinds: ["sovereign_state", "supranational"],
          limit: 500,
        }).then((found) => found.polities),
      ]);
      const ids = [
        election.id,
        election.polity,
        ...tops.rows.map((row) => row.id),
      ];
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      const home = slugs.get(election.id) ?? null;
      return {
        polity: {
          name: labels.full.get(election.polity) ?? "",
          path: slugs.get(election.polity) ?? null,
        },
        election: {
          name: labels.full.get(election.id) ?? "",
          date: election.date,
          path: home,
        },
        races: lineup.flatMap((race) => {
          const at =
            race.path ??
            (race.count >= 3 && home != null ? `${home}/${race.anchor}` : null);
          return at == null ? [] : [{ name: race.name, path: at }];
        }),
        series: timeline,
        elections: tops.rows.flatMap((row) => {
          const at = slugs.get(row.id);
          return at == null
            ? []
            : [
                {
                  name: labels.full.get(row.id) ?? "",
                  date: row.date,
                  path: at,
                },
              ];
        }),
        polities: countries
          .flatMap((country) =>
            country.latest?.path == null
              ? []
              : [{ name: country.name, path: country.latest.path }],
          )
          .toSorted((a, b) => a.name.localeCompare(b.name)),
      };
    }),
  places: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/places/{id}",
        summary:
          "Find the places a contest reports by name, accents and case ignored, with the places each one sits inside, for searching a map before its finest places have loaded",
      }),
    )
    .input(
      v.object({
        id: Id,
        q: v.pipe(v.string(), v.trim(), v.minLength(2), v.maxLength(80)),
      }),
    )
    .output(
      v.array(
        v.object({
          k: v.number(),
          name: v.string(),
          within: v.nullable(v.string()),
          above: v.array(v.number()),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const pattern = `%${input.q.replaceAll(/[\\%_]/gu, (found) => `\\${found}`)}%`;
      const found = await db.execute<{
        k: number;
        name: string;
        within: string | null;
        above: Array<number>;
      }>(sql`
        with recursive grp(id) as (
          select ${input.id}::uuid
          union all
          select c.id from contest c join grp on c.allocation_parent_id = grp.id
        ),
        sets as (
          select rs.k from grp join result_set rs on rs.contest_id = grp.id and rs.status = 'active' and ${served("rs")}
        ),
        held as (
          select distinct r.area_k as k from sets
          cross join lateral (select x.area_k from result_rollup x where x.result_set_k = sets.k offset 0) r
        ),
        matched as (
          select distinct on (a.k) a.id, a.k, n.text as name from held h
          join area a on a.k = h.k
          join name n on n.entity_id = a.id
          where unaccent(n.text) ilike unaccent(${pattern})
          order by a.k, n.is_preferred desc, n.recorded_from desc
        ),
        hits as (
          select * from matched
          order by lower(name) = lower(${input.q}) desc, length(name), name
          limit 12
        )
        select h.k, h.name,
          (select n.text from area_parent ap join name n on n.entity_id = ap.parent_id where ap.area_id = h.id order by n.is_preferred desc, n.recorded_from desc limit 1) as within,
          coalesce((
            with recursive up(id, depth) as (
              select h.id, 0
              union all
              select ap.parent_id, up.depth + 1 from area_parent ap join up on ap.area_id = up.id where up.depth < 6
            )
            select array_agg(a.k order by up.depth) from up join area a on a.id = up.id where up.depth > 0
          ), '{}') as above
        from hits h
      `);
      return found.rows;
    }),
};
