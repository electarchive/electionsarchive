import { openapi } from "@orpc/openapi";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import layout2010 from "#lib/server/hex/gb-pcon-2010.json";
import layout2024 from "#lib/server/hex/gb-pcon-2024.json";
import cartograms from "#lib/server/hex/us-cd.json";
import { inside, latest, names, paths, refs } from "#lib/server/rpc/archive.js";

const fold = (key: string) => (key.startsWith("person:") ? "independent" : key);

const CARTOGRAMS = Object.fromEntries(
  Object.entries(
    import.meta.glob<{
      credit: string;
      districts: Record<string, string>;
      bounds: Record<string, [number, number, number, number]>;
      points: Record<string, [number, number]>;
      states: Record<string, string>;
      labels: Record<string, [number, number, string]>;
    }>("../hex/us-cd-[0-9]*.json", { eager: true, import: "default" }),
  ).map(([file, layout]) => [file.slice(-9, -5), layout]),
);
const CARTOGRAM_YEARS: Record<string, number> = cartograms;
const TILES = import.meta.glob<{
  grids: Record<
    string,
    {
      boundaries: { states: Array<[number, number, number]> };
      labels: Record<string, [number, number, string]>;
    }
  >;
  years: Record<
    string,
    { grid: string; hexes: Record<string, [number, number]> }
  >;
}>("../hex/us-cd-tiles.json", { eager: true, import: "default" })[
  "../hex/us-cd-tiles.json"
];
const SHRINK = Math.sqrt((3 * Math.sqrt(3)) / 2) / 50;
const shrink = (value: number) => Math.round(value * SHRINK * 100) / 100;
const shrunk = (path: string) =>
  path.replaceAll(/-?\d+/g, (number) => String(shrink(Number(number))));

const unitedStates = async (id: string, within: string | undefined) => {
  const found = await db.execute<{ k: number; code: string; year: number }>(sql`
    with recursive tree(id) as (
      select e.id from election e join slug ps on ps.entity_id = e.polity_id and ps.is_current and (ps.path = 'us' or ps.path like 'us/%') where e.id = ${id}::uuid
      union all
      select e.id from election e join tree on e.parent_id = tree.id where e.kind not in ('runoff', 'indirect')
    )
    select distinct a.k, ac.code, extract(year from e.date_from)::int as year
    from contest c join tree on tree.id = c.election_id
    join election e on e.id = c.election_id
    join area a on a.id = c.district_area_id and a.scheme_code = 'us.cd'
    join area_code ac on ac.area_id = a.id and (ac.valid @> e.date_from or ac.valid is null)
    where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
      and coalesce(c.magnitude, 1) = 1 and not c.notional
      and ${within == null ? sql`true` : inside(within, sql`c.district_area_id`, sql`e.date_from`)}
  `);
  const first = found.rows[0];
  if (first == null) {
    return null;
  }
  const year = String(first.year - (first.year % 2));
  const placed = TILES?.years[year];
  const grid = placed == null ? null : TILES?.grids[placed.grid];
  if (placed == null || grid == null) {
    return null;
  }
  const drawn = CARTOGRAMS[String(CARTOGRAM_YEARS[year] ?? "")];
  const states = new Set(found.rows.map((row) => row.code.slice(0, 2)));
  const cells = found.rows.flatMap((row) => {
    const hex = placed.hexes[row.code];
    return hex == null
      ? []
      : [{ k: row.k, q: hex[0], r: hex[1], isApart: row.code.endsWith("98") }];
  });
  const kept = new Set(cells.map((cell) => `${cell.q},${cell.r}`));
  const bounds = found.rows.flatMap((row) => {
    const box = drawn?.bounds[row.code];
    return box == null ? [] : [box];
  });
  const seats = new Map<string, number>();
  const codes = Object.keys(drawn?.districts ?? {});
  for (const code of codes) {
    seats.set(code.slice(0, 2), (seats.get(code.slice(0, 2)) ?? 0) + 1);
  }
  return {
    credit: null,
    cells,
    borders: [
      {
        isNation: true,
        edges: grid.boundaries.states.filter(([q, r, side]) => {
          const odd = r & 1;
          const [nq, nr] =
            side === 1
              ? [q + odd, r + 1]
              : side === 2
                ? [q + 1, r]
                : [q + odd, r - 1];
          return kept.has(`${q},${r}`) && kept.has(`${nq},${nr}`);
        }),
      },
    ],
    labels: Object.entries(grid.labels).flatMap(([fips, [q, r, text]]) =>
      states.has(fips) && states.size > 1 ? [{ q, r, text }] : [],
    ),
    cartogram:
      drawn == null
        ? null
        : {
            credit: drawn.credit,
            box: [
              Math.min(...bounds.map((box) => box[0])),
              Math.min(...bounds.map((box) => box[1])),
              Math.max(...bounds.map((box) => box[2])),
              Math.max(...bounds.map((box) => box[3])),
            ].map((value) => shrink(value)),
            districts: found.rows.flatMap((row) => {
              const d = drawn.districts[row.code];
              const point = drawn.points[row.code];
              return d == null || point == null
                ? []
                : [
                    {
                      k: row.k,
                      d: shrunk(d),
                      x: shrink(point[0]),
                      y: shrink(point[1]),
                    },
                  ];
            }),
            states: Object.entries(drawn.states).flatMap(([fips, d]) =>
              states.has(fips) ? [shrunk(d)] : [],
            ),
            labels: Object.entries(drawn.labels).flatMap(
              ([fips, [x, y, text]]) =>
                states.has(fips) &&
                states.size > 1 &&
                (seats.get(fips) ?? 0) > 2
                  ? [{ x: shrink(x), y: shrink(y), text }]
                  : [],
            ),
          },
  };
};

export const seats = {
  council: os
    .meta(
      openapi({
        method: "GET",
        path: "/bodies/{id}/council",
        summary:
          "A local council seat by seat: each seat's holders from the elections that filled it, each election's re-elected members, open seats, defeated incumbents, and runoffs, and the council's size over time",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          isPartisan: v.boolean(),
          parties: v.array(
            v.object({
              id: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
            }),
          ),
          seats: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              isCurrent: v.boolean(),
              holders: v.array(
                v.object({
                  person: v.nullable(v.string()),
                  name: v.string(),
                  party: v.nullable(v.string()),
                  from: v.string(),
                  until: v.nullable(v.string()),
                  wins: v.number(),
                  via: v.picklist(["elected", "runoff", "special"]),
                  path: v.nullable(v.string()),
                }),
              ),
            }),
          ),
          elections: v.array(
            v.object({
              id: v.string(),
              date: v.string(),
              when: v.string(),
              name: v.string(),
              path: v.nullable(v.string()),
              isSpecial: v.boolean(),
              isMinor: v.boolean(),
              up: v.number(),
              filled: v.array(v.string()),
              held: v.number(),
              open: v.number(),
              defeated: v.number(),
              runoffs: v.number(),
              changes: v.array(
                v.object({
                  seat: v.string(),
                  path: v.nullable(v.string()),
                  kind: v.picklist(["open", "defeated", "new"]),
                  isRunoff: v.boolean(),
                  winner: v.object({
                    person: v.nullable(v.string()),
                    name: v.string(),
                    party: v.nullable(v.string()),
                  }),
                  previous: v.nullable(
                    v.object({
                      person: v.nullable(v.string()),
                      name: v.string(),
                      party: v.nullable(v.string()),
                    }),
                  ),
                }),
              ),
            }),
          ),
          sizes: v.array(
            v.object({
              seats: v.number(),
              from: v.string(),
              to: v.nullable(v.string()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const id = input.id.toLowerCase();
      const found = await db.execute<{
        contest: string;
        election: string;
        date: string;
        when: string;
        isSpecial: boolean;
        isPrimary: boolean;
        kind: string;
        round: number;
        prior: string | null;
        area: string | null;
        isWide: boolean;
        segment: string;
        person: string | null;
        label: string | null;
        party: string | null;
        outcome: string | null;
        votes: number | null;
      }>(sql`
        with cs as materialized (
          select c.id, c.election_id, e.date_from, case e.date_precision when 'month' then to_char(e.date_from, 'YYYY-MM') when 'year' then to_char(e.date_from, 'YYYY') else e.date_from::text end as "when", e.kind = 'special' or c.term = 'remainder' as special, e.kind = 'primary' as primary,
            c.round_number, c.previous_round_id, c.district_area_id,
            c.district_area_id is null or c.district_area_id = (
              select pt.area_id from polity_territory pt where pt.polity_id = b.polity_id and pt.valid @> e.date_from limit 1
            ) as wide,
            (select split_part(s.path, '/', -1) from slug s where s.entity_id = c.id and s.is_current limit 1) as segment
          from body b
          join polity p on p.id = b.polity_id and p.kind in ('municipality', 'submunicipal', 'county', 'school_district', 'special_district')
          join contest c on c.body_id = b.id
          join election e on e.id = c.election_id and e.status = 'held'
          where b.id = ${id}::uuid and c.allocation_parent_id is null and c.party_scope_id is null
            and not c.notional and c.kind = 'body_seats' and c.magnitude = 1
        )
        select cs.id::text as contest, cs.election_id::text as election, cs.date_from::text as date, cs."when", cs.special as "isSpecial",
          cs.primary as "isPrimary", o.kind::text as kind, cs.round_number as round, cs.previous_round_id::text as prior, cs.district_area_id::text as area, cs.wide as "isWide",
          coalesce(cs.segment, '') as segment, ca.person_id::text as person, coalesce(ca.ballot_name, o.ballot_label) as label,
          coalesce(o.party_id, ca.member_of_party_id, (
            select k.party_id from contest_option k where k.parent_option_id = o.id and k.party_id is not null
            order by k.ballot_position nulls last, k.k limit 1
          ))::text as party,
          w.kind::text as outcome, t.votes
        from cs
        join contest_option o on o.contest_id = cs.id and o.parent_option_id is null
        left join candidacy ca on ca.option_id = o.id
        left join lateral (
          select rs.id, rs.k from result_set rs where rs.contest_id = cs.id and ${latest("rs")} limit 1
        ) rs on true
        left join lateral (
          select w.kind from outcome w where w.result_set_id = rs.id and w.option_id = o.id limit 1
        ) w on true
        left join lateral (
          select sum(y.value)::float8 as votes from area a
          cross join lateral (
            select y.value from result_rollup y
            where y.result_set_k = rs.k and y.area_k = a.k and y.option_k = o.k and y.measure = 1
              and y.count_round = (select max(z.count_round) from result_rollup z where z.result_set_k = rs.k and z.area_k = a.k and z.measure = 1 offset 0)
            offset 0
          ) y
          where a.id = cs.district_area_id
        ) t on true
      `);
      if (found.rows.length === 0) {
        return null;
      }
      const contests = Map.groupBy(found.rows, (row) => row.contest);
      const later = new Set(found.rows.flatMap((row) => row.prior ?? []));
      const [labels, slugs, colors] = await Promise.all([
        names([
          ...new Set([
            id,
            ...found.rows.flatMap((row) => [
              row.election,
              row.contest,
              ...(row.area == null ? [] : [row.area]),
              ...(row.person == null ? [] : [row.person]),
            ]),
          ]),
        ]),
        paths([
          ...new Set(found.rows.flatMap((row) => [row.contest, row.election])),
        ]),
        refs(found.rows.flatMap((row) => row.party ?? [])),
      ]);
      const body = labels.full.get(id) ?? "";
      const strip = (text: string) =>
        text.startsWith(`${body}, `) ? text.slice(body.length + 2) : text;
      const first = (contest: string) => {
        let current = contests.get(contest)?.[0];
        while (current?.prior != null && contests.has(current.prior)) {
          current = contests.get(current.prior)?.[0];
        }
        return current;
      };
      const person = (row: (typeof found.rows)[number]) => ({
        person: row.person,
        name: labels.full.get(row.person ?? "") ?? row.label ?? "",
        party: row.party,
      });
      const decided = contests
        .entries()
        .filter(([contest]) => !later.has(contest))
        .flatMap(([contest, rows]) => {
          let sum = 0;
          for (const row of rows) {
            sum += row.votes ?? 0;
          }
          const ranked = rows
            .filter((row) => row.kind === "candidate")
            .toSorted((a, b) => (b.votes ?? 0) - (a.votes ?? 0));
          const declared = ranked.find(
            (row) => row.outcome === "elected" || row.outcome === "won",
          );
          const top = ranked[0];
          const isRunoff = (rows[0]?.round ?? 1) > 1;
          const isOpen =
            rows[0]?.isPrimary !== true &&
            rows.every((row) => row.outcome == null);
          const winner =
            declared ??
            (isOpen &&
            top != null &&
            (isRunoff || ranked.length === 1 || (top.votes ?? 0) > 0.5 * sum)
              ? top
              : undefined);
          const head = first(contest);
          if (winner == null || head == null) {
            return [];
          }
          const ran = [
            ...rows,
            ...(head.contest === contest
              ? []
              : (contests.get(head.contest) ?? [])),
          ].map((row) => person(row));
          const key = head.isWide
            ? head.segment.replace(/-short-term$/u, "")
            : (labels.full.get(head.area ?? "") ?? head.segment);
          const label = head.isWide
            ? (labels.full.get(head.contest) ?? head.segment)
                .replace(/^\d{4} /u, "")
                .replace(`${body} election, `, "")
                .replace(`${body}, `, "")
                .replace(/ election$/u, "")
            : strip(labels.full.get(head.area ?? "") ?? head.segment);
          return [
            {
              key,
              label,
              contest,
              path: slugs.get(contest) ?? null,
              election: head.election,
              date: head.date,
              when: head.when,
              decidedOn: rows[0]?.date ?? head.date,
              isSpecial: head.isSpecial,
              isMinor: head.isSpecial,
              isWide: head.isWide,
              isRunoff,
              winner: person(winner),
              ran,
            },
          ];
        })
        .toArray()
        .toSorted((a, b) => a.decidedOn.localeCompare(b.decidedOn));
      const norm = (name: string) => {
        const tokens = name
          .normalize("NFD")
          .replaceAll(/\p{M}/gu, "")
          .toLocaleLowerCase()
          .replaceAll(/(['"‘’“”]).*?\1|\(.*?\)/gu, " ")
          .split(/[^\p{L}\p{N}]+/u)
          .filter(
            (token) =>
              token.length > 1 &&
              !["jr", "sr", "ii", "iii", "iv"].includes(token),
          );
        return tokens.length < 2
          ? name.toLocaleLowerCase()
          : `${tokens[0] ?? ""} ${tokens.at(-1) ?? ""}`;
      };
      const same = (
        a: { person: string | null; name: string },
        b: { person: string | null; name: string },
      ) =>
        (a.person != null && a.person === b.person) ||
        (a.name !== "" && norm(a.name) === norm(b.name));
      const filled = Map.groupBy(decided, (entry) => entry.election);
      let typical = 0;
      for (const rows of filled.values()) {
        typical = Math.max(typical, rows.length);
      }
      for (const rows of filled.values()) {
        if (rows.length <= Math.max(2, 0.15 * typical)) {
          for (const entry of rows) {
            entry.isMinor = true;
          }
        }
      }
      const seats = new Map<
        string,
        {
          key: string;
          label: string;
          isWide: boolean;
          last: string;
          holders: Array<{
            person: string | null;
            name: string;
            party: string | null;
            from: string;
            until: string | null;
            wins: number;
            via: "elected" | "runoff" | "special";
            path: string | null;
          }>;
        }
      >();
      const elections = new Map<
        string,
        {
          id: string;
          date: string;
          when: string;
          name: string;
          path: string | null;
          isSpecial: boolean;
          isMinor: boolean;
          up: number;
          filled: Array<string>;
          held: number;
          open: number;
          defeated: number;
          runoffs: number;
          changes: Array<{
            seat: string;
            path: string | null;
            kind: "open" | "defeated" | "new";
            isRunoff: boolean;
            winner: {
              person: string | null;
              name: string;
              party: string | null;
            };
            previous: {
              person: string | null;
              name: string;
              party: string | null;
            } | null;
          }>;
        }
      >();
      const opening = decided[0]?.date ?? "";
      for (const entry of decided) {
        const seat = seats.get(entry.key) ?? {
          key: entry.key,
          label: entry.label,
          isWide: entry.isWide,
          last: entry.date,
          holders: [],
        };
        seats.set(entry.key, seat);
        const before = seat.holders.at(-1);
        const election = elections.get(entry.election) ?? {
          id: entry.election,
          date: entry.date,
          when: entry.when,
          name: labels.full.get(entry.election) ?? "",
          path: slugs.get(entry.election) ?? null,
          isSpecial: entry.isSpecial,
          isMinor: entry.isMinor,
          up: 0,
          filled: [],
          held: 0,
          open: 0,
          defeated: 0,
          runoffs: 0,
          changes: [],
        };
        elections.set(entry.election, election);
        election.up += 1;
        election.filled.push(entry.label);
        election.runoffs += Number(entry.isRunoff);
        const isSame = before != null && same(before, entry.winner);
        if (before != null && isSame) {
          election.held += 1;
          before.wins += 1;
        } else {
          if (before != null) {
            before.until = entry.decidedOn;
          }
          seat.holders.push({
            ...entry.winner,
            from: entry.decidedOn,
            until: null,
            wins: 1,
            via: entry.isSpecial
              ? "special"
              : entry.isRunoff
                ? "runoff"
                : "elected",
            path: entry.path,
          });
          if (before == null && entry.date === opening) {
            continue;
          }
          const kind =
            before == null
              ? ("new" as const)
              : entry.ran.some((row) => same(row, before))
                ? ("defeated" as const)
                : ("open" as const);
          election.open += Number(kind !== "defeated");
          election.defeated += Number(kind === "defeated");
          election.changes.push({
            seat: entry.label,
            path: entry.path,
            kind,
            isRunoff: entry.isRunoff,
            winner: entry.winner,
            previous:
              before == null
                ? null
                : {
                    person: before.person,
                    name: before.name,
                    party: before.party,
                  },
          });
        }
        if (!entry.isMinor) {
          seat.last = entry.date;
        }
      }
      const regular = decided.filter((entry) => !entry.isMinor);
      const spans = new Map<string, { first: number; last: number }>();
      const gaps: Array<number> = [];
      for (const [key, rows] of Map.groupBy(regular, (entry) => entry.key)) {
        const dates = [...new Set(rows.map((entry) => Date.parse(entry.date)))];
        for (let at = 1; at < dates.length; at += 1) {
          gaps.push((dates[at] ?? 0) - (dates[at - 1] ?? 0));
        }
        spans.set(key, { first: dates[0] ?? 0, last: dates.at(-1) ?? 0 });
      }
      const window =
        (gaps.toSorted((a, b) => a - b)[Math.floor(gaps.length * 0.75)] ??
          4 * 3.156e10) * 1.25;
      const newest = Date.parse(regular.at(-1)?.date ?? "");
      const start = Date.parse(regular[0]?.date ?? "") + window / 1.25 - 3.9e9;
      const sizes: Array<{ seats: number; from: string; to: string | null }> =
        [];
      for (const day of new Set(regular.map((entry) => entry.date))) {
        const at = Date.parse(day);
        if (at < start) {
          continue;
        }
        let alive = 0;
        for (const span of spans.values()) {
          alive += Number(span.first <= at && at - span.last < window);
        }
        const last = sizes.at(-1);
        if (last?.seats === alive) {
          continue;
        }
        if (last != null && last.from.slice(0, 4) === day.slice(0, 4)) {
          sizes.pop();
        }
        const prior = sizes.at(-1);
        if (prior?.seats === alive) {
          prior.to = null;
          continue;
        }
        if (prior != null) {
          prior.to = day;
        }
        sizes.push({ seats: alive, from: day, to: null });
      }
      const winners = regular
        .filter((entry) => entry.date === regular.at(-1)?.date)
        .map((entry) => entry.winner);
      const isPartisan =
        winners.filter((entry) => entry.party != null).length >
        0.5 * winners.length;
      return {
        isPartisan,
        parties: [...colors.values()],
        seats: seats
          .values()
          .toArray()
          .toSorted(
            (a, b) =>
              Number(a.isWide) - Number(b.isWide) ||
              a.label.localeCompare(b.label, undefined, { numeric: true }),
          )
          .map((seat) => ({
            key: seat.key,
            label: seat.label,
            isCurrent: newest - Date.parse(seat.last) < window,
            holders: seat.holders,
          })),
        elections: elections
          .values()
          .toArray()
          .toSorted((a, b) => b.date.localeCompare(a.date)),
        sizes,
      };
    }),
  gains: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/gains",
        summary:
          "Count the single-member seats each party gained from another since the chamber's previous general election",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.nullable(
        v.object({
          since: v.number(),
          path: v.nullable(v.string()),
          total: v.number(),
          compared: v.number(),
          estimated: v.number(),
          isNotional: v.optional(v.boolean()),
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
        now: Array<string>;
        before: Array<string>;
        since: string | null;
        day: string | null;
        election: string | null;
        notional: boolean;
        isChair: boolean;
        others: Array<string>;
      }>(sql`
        with recursive tree(id) as (
          select e.id from election e
          join slug ps on ps.entity_id = e.polity_id and ps.is_current and (ps.path in ('gb', 'us', 'ie') or ps.path like 'gb/%' or ps.path like 'us/%')
          where e.id = ${input.id}::uuid and e.kind <> 'by_election'
          union all
          select e.id from election e join tree on e.parent_id = tree.id where e.kind not in ('runoff', 'indirect')
        ),
        now as materialized (
          select c.id, c.body_id as body, c.district_area_id as area, e.date_from as day,
            coalesce(bs.as_of, e.date_from) as drawn, coalesce(c.magnitude, 1) as magnitude
          from contest c join tree on tree.id = c.election_id join election e on e.id = c.election_id
          join body b on b.id = c.body_id join slug bp on bp.entity_id = b.polity_id and bp.is_current
          left join boundary_set bs on bs.id = c.boundary_set_id
          where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and (coalesce(c.magnitude, 1) = 1 or bp.path = 'ie')
            and ((bp.path = 'us' and b.kind = 'lower_house') or not (bp.path = 'us' or bp.path like 'us/%'))
            and ${input.within == null ? sql`true` : inside(input.within, sql`c.district_area_id`, sql`e.date_from`)}
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
          select c.id, c.district_area_id as area, e.date_from as day, coalesce(bs.as_of, e.date_from) as drawn,
            coalesce(c.magnitude, 1) as magnitude
          from contest c join election e on e.id = c.election_id
          left join boundary_set bs on bs.id = c.boundary_set_id
          where c.body_id = (select body from chamber) and e.kind = 'general'
            and e.date_from between (select day from last) - 200 and (select day from last)
            and c.notional = exists (
              select from contest nc join election ne on ne.id = nc.election_id
              where nc.notional and nc.body_id = (select body from chamber)
                and ne.date_from between (select day from last) - 200 and (select day from last)
                and nc.district_area_id in (select area from now)
            )
            and c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and (coalesce(c.magnitude, 1) = 1 or exists (select from now where magnitude > 1))
        ),
        kept as materialized (
          select distinct on (n.id) n.id, p.id as prior, not exists (
            select from area_geometry gp
            join area_geometry gn on gn.area_id = n.area and gn.valid @> n.drawn
            where gp.area_id = p.area and gp.valid @> p.drawn and gp.valid <> gn.valid
              and ST_Area(ST_Intersection(gp.geom, gn.geom, 0.00001)) < 0.97 * greatest(ST_Area(gp.geom), ST_Area(gn.geom))
          ) as same
          from now n join prev p on p.area = n.area and p.magnitude = n.magnitude
          where n.body = (select body from chamber)
          order by n.id, p.drawn desc
        ),
        paired as materialized (
          select n.id, n.area, coalesce(k.same, g.same, false) as same, coalesce(
            k.prior,
            g.id,
            (select p.id from prev p
              where p.magnitude = n.magnitude and exists (
                select from name a join name b on lower(a.text) = lower(b.text)
                where a.entity_id = n.area and b.entity_id = p.area
              ) limit 1)
          ) as prior
          from now n left join kept k on k.id = n.id
          left join lateral (
            select p.id, ST_Area(x.geom) >= 0.97 * greatest(ST_Area(gp.geom), ST_Area(gn.geom)) as same
            from prev p
            join area_geometry gp on gp.area_id = p.area and gp.valid @> p.drawn
            join area_geometry gn on gn.area_id = n.area and gn.valid @> n.drawn
            cross join lateral (select ST_Intersection(gp.geom, gn.geom, 0.00001) as geom) x
            where p.magnitude = n.magnitude and gp.geom && gn.geom and ST_Intersects(gp.geom, gn.geom)
              and (n.magnitude = 1 or ST_Area(x.geom) > 0.5 * ST_Area(gn.geom))
            order by ST_Area(x.geom) desc limit 1
          ) g on k.prior is null
          where n.body = (select body from chamber)
        ),
        top as materialized (
          select rs.contest_id as contest, rs.k as set, e.date_from as day, o.id, o.k, o.party_id
          from result_set rs
          join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          join contest_option o on o.id = x.option_id
          join election e on e.id = rs.election_id
          where ${latest("rs")} and rs.contest_id in (select id from paired union select prior from paired)
          union all
          select rs.contest_id, rs.k, e.date_from, o.id, o.k, o.party_id
          from contest nc
          join area na on na.id = nc.district_area_id
          join election e on e.id = nc.election_id
          join result_set rs on rs.contest_id = nc.id and rs.is_default and rs.stage = 'notional'
          cross join lateral (
            select o.id, o.k, o.party_id from contest_option o
            join result_rollup y on y.result_set_k = rs.k and y.area_k = na.k and y.option_k = o.k
              and y.measure = 1 and y.channel = 0 and y.count_round = 0
            where o.contest_id = nc.id
            order by y.value desc limit 1
          ) o
          where nc.notional and nc.id in (select prior from paired)
        ),
        won as (
          select distinct on (t.contest, t.k) t.contest, t.k,
            coalesce(bm.into_id, br.parent_party_id, lin.party, sp.party, pm.into_id, lp.party)::text as party,
            cy.person_id::text as person
          from top t
          cross join lateral (
            select coalesce(t.party_id, (
              select ch.party_id from contest_option ch
              join contest lc on lc.id = ch.contest_id join area la on la.id = lc.district_area_id
              join result_rollup y on y.result_set_k = t.set and y.area_k = la.k and y.option_k = ch.k
              where t.party_id is null and ch.parent_option_id = t.id and ch.party_id is not null
              order by y.value desc limit 1
            )) as party
          ) lp
          left join candidacy cy on cy.option_id = t.id and cy.role = 'head'
          left join entity_merge pm on pm.entity_id = lp.party
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, lp.party)
            and br.kind = 'regional_branch' and br.valid @> t.day
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          left join lateral (
            select coalesce(nm.into_id, d.new) as party from party_relation r
            cross join lateral (
              select case when r.kind = 'successor_of' then r.to_party_id else r.from_party_id end as old,
                case when r.kind = 'successor_of' then r.from_party_id else r.to_party_id end as new
            ) d
            left join entity_merge nm on nm.entity_id = d.new
            where t.contest in (select prior from paired)
              and r.kind in ('merged_into', 'absorbed_by', 'successor_of')
              and d.old = coalesce(pm.into_id, lp.party)
              and r.date > t.day and r.date <= (select min(day) from now) + 365
              and d.old not in (select x.party_id from top x where x.contest in (select id from paired) and x.party_id is not null)
            limit 1
          ) lin on true
          left join lateral (
            select coalesce(xm.into_id, xo.party_id) as party
            from candidacy xc
            join contest_option xo on xo.id = xc.option_id
            join contest xk on xk.id = xo.contest_id
            join election xe on xe.id = xk.election_id
            left join entity_merge xm on xm.entity_id = xo.party_id
            join party xp on xp.id = coalesce(xm.into_id, xo.party_id)
            where t.contest in (select prior from paired)
              and exists (
                select from party sx join name sn on sn.entity_id = sx.id and sn.text = 'Speaker'
                where sx.kind = 'independent_group' and sx.id = coalesce(pm.into_id, lp.party)
              )
              and xc.person_id = cy.person_id and xe.date_from < t.day
              and not exists (
                select from top n join candidacy nc on nc.option_id = n.id and nc.role = 'head'
                where n.contest in (select id from paired) and nc.person_id = cy.person_id
              )
              and not (xp.kind = 'independent_group' and exists (select from name sn where sn.entity_id = xp.id and sn.text = 'Speaker'))
            order by xe.date_from desc limit 1
          ) sp on true
          order by t.contest, t.k
        )
        select p.id, p.area, p.same,
          array(select coalesce(w.party, 'person:' || w.person) from won w where w.contest = p.id order by w.k) as now,
          array(select coalesce(b.party, 'person:' || b.person) from won b where b.contest = p.prior order by b.k) as before,
          (select day::text from last) as since,
          (select min(day)::text from now) as day,
          (select election::text from last) as election,
          exists (select from paired x join contest c on c.id = x.prior where c.notional) as notional,
          exists (
            select from won w
            join won b on b.contest = p.prior and b.person = w.person
            join party sx on sx.id::text = w.party and sx.kind = 'independent_group'
            join name sn on sn.entity_id = sx.id and sn.text = 'Speaker'
            where w.contest = p.id
          ) as "isChair",
          array(
            select x.id::text from party x
            where x.kind = 'independent_group' and x.id::text in (select w.party from won w where w.contest in (p.id, p.prior))
          ) as others
        from paired p
        order by p.id
      `);
      const since = found.rows[0]?.since;
      const seated: Array<{
        id: string;
        area: string;
        same: boolean;
        now: string | null;
        before: string | null;
      }> = [];
      for (const row of found.rows) {
        const kept = [...row.before];
        const gained: Array<string> = [];
        const now = row.isChair ? row.before : row.now;
        if (now.length === 0) {
          seated.push({ ...row, now: null, before: row.before[0] ?? null });
        }
        for (const key of now) {
          const at = kept.indexOf(key);
          if (at === -1) {
            gained.push(key);
          } else {
            seated.push({ ...row, now: key, before: key });
            kept.splice(at, 1);
          }
        }
        for (const [index, key] of gained.entries()) {
          seated.push({ ...row, now: key, before: kept[index] ?? null });
        }
      }
      const compared = seated.filter(
        (row) => row.now != null && row.before != null,
      );
      if (since == null || compared.length === 0) {
        return null;
      }
      const others = new Set(found.rows.flatMap((row) => row.others));
      const isOther = (key: string | null) =>
        key != null && (key.startsWith("person:") || others.has(key));
      const isMoved = (row: (typeof compared)[number]) =>
        row.now !== row.before && !(isOther(row.now) && isOther(row.before));
      const held = (side: "now" | "before", key: string) =>
        compared.filter((row) => row[side] === key).length;
      const keys = [
        ...new Set(
          compared.flatMap((row) => [row.now ?? "", row.before ?? ""]),
        ),
      ].toSorted(
        (a, b) =>
          held("now", b) - held("now", a) ||
          held("before", b) - held("before", a) ||
          a.localeCompare(b),
      );
      const changed = compared.filter(isMoved);
      const parties = keys.filter((key) => !key.startsWith("person:"));
      const people = keys.flatMap((key) =>
        key.startsWith("person:") ? [key.slice(7)] : [],
      );
      const [colored, labels, slugs, previous] = await Promise.all([
        refs(parties, found.rows[0]?.day ?? since),
        names([...people, ...changed.map((row) => row.area)], since),
        paths(changed.map((row) => row.id)),
        paths([found.rows[0]?.election ?? ""]),
      ]);
      const flows = Map.groupBy(
        compared,
        (row) => `${row.now}|${isMoved(row) ? row.before : row.now}`,
      )
        .entries()
        .map(([pair, rows]) => {
          const [to = "", from = ""] = pair.split("|", 2);
          return { to, from, seats: rows.length };
        })
        .toArray()
        .toSorted(
          (a, b) =>
            b.seats - a.seats ||
            a.to.localeCompare(b.to) ||
            a.from.localeCompare(b.from),
        );
      return {
        since: Number(since.slice(0, 4)),
        path: previous.get(found.rows[0]?.election ?? "") ?? null,
        total: seated.length,
        compared: compared.length,
        estimated: compared.filter((row) => !row.same).length,
        isNotional: found.rows[0]?.notional === true,
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
          "Say whether a single-member seat's winning party held it or gained it since the seat's previous result: the chamber's previous general election, or a later by-election",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          since: v.number(),
          isGain: v.boolean(),
          isOther: v.boolean(),
          lead: v.picklist(["majority", "margin"]),
          basis: v.picklist(["notional", "same", "area", "name"]),
          party: v.nullable(
            v.object({ label: v.string(), short: v.nullable(v.string()) }),
          ),
          from: v.nullable(
            v.object({ label: v.string(), short: v.nullable(v.string()) }),
          ),
          prior: v.object({ name: v.string(), path: v.nullable(v.string()) }),
          alliance: v.nullable(v.string()),
          vacated: v.optional(
            v.nullable(
              v.object({ name: v.string(), via: v.nullable(v.string()) }),
            ),
          ),
          seats: v.optional(
            v.array(
              v.object({
                isGain: v.boolean(),
                count: v.number(),
                change: v.optional(v.number()),
                party: v.nullable(
                  v.object({
                    label: v.string(),
                    short: v.nullable(v.string()),
                  }),
                ),
                from: v.nullable(
                  v.object({
                    label: v.string(),
                    short: v.nullable(v.string()),
                  }),
                ),
              }),
            ),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        basis: "notional" | "same" | "area" | "name";
        prior: string;
        area: string;
        since: string;
        day: string;
        now: Array<string>;
        before: Array<string>;
        isUs: boolean;
        isIe: boolean;
        alliance: string | null;
        member: string | null;
        via: string | null;
        isOther: boolean;
      }>(sql`
        with this as materialized (
          select c.id, c.body_id as body, c.district_area_id as area, e.date_from as day, e.kind, coalesce(c.magnitude, 1) as magnitude,
            coalesce(bs.as_of, e.date_from) as drawn, bp.path = 'us' as "isUs", bp.path = 'ie' as "isIe"
          from contest c join election e on e.id = c.election_id
          join slug ps on ps.entity_id = e.polity_id and ps.is_current and (ps.path in ('gb', 'us', 'ca', 'ie') or ps.path like 'gb/%' or ps.path like 'us/%')
          join body b on b.id = c.body_id join slug bp on bp.entity_id = b.polity_id and bp.is_current
          left join boundary_set bs on bs.id = c.boundary_set_id
          where c.id = ${input.id}::uuid and c.kind = 'body_seats' and c.allocation_parent_id is null
            and c.party_scope_id is null and c.district_area_id is not null
            and ((bp.path = 'us' and b.kind = 'lower_house') or not (bp.path = 'us' or bp.path like 'us/%'))
            and not (bp.path = 'ie' and e.kind = 'by_election')
        ),
        last as (
          select e.date_from as day, coalesce(e.parent_id, e.id) as election
          from this t join contest c on c.body_id = t.body join election e on e.id = c.election_id
          where e.status = 'held'
            and ((t.kind <> 'local' and e.kind = 'general' and e.date_from < t.day - case when t.kind = 'general' then 120 else 0 end)
              or (t.kind = 'local' and e.kind = 'local' and e.date_from <= t.day - interval '42 months'))
          order by e.date_from desc limit 1
        ),
        prev as materialized (
          select c.id, c.district_area_id as area, coalesce(bs.as_of, e.date_from) as drawn, c.notional,
            case when e.kind = 'by_election' then e.date_from else (select day from last) end as day
          from contest c join election e on e.id = c.election_id
          left join boundary_set bs on bs.id = c.boundary_set_id
          where c.body_id = (select body from this) and (not c.notional or c.district_area_id = (select area from this))
            and ((e.kind = case when (select kind from this) = 'local' then 'local' else 'general' end::election_kind
              and e.date_from between (select day from last) - 200 and (select day from last))
              or (e.kind = 'by_election' and (select kind from this) = 'by_election'
                and e.date_from > (select day from last) and e.date_from < (select day from this)))
            and c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and (coalesce(c.magnitude, 1) = (select magnitude from this)
              or ((select kind from this) = 'local' and (select magnitude from this) = 1 and c.magnitude > 1))
        ),
        paired as (
          select t.id, coalesce(s.id, g.id, n.id) as prior, coalesce(s.area, g.area, n.area) as area,
            coalesce(s.day, g.day, n.day) as day,
            case when s.notional then 'notional' when s.same or g.same then 'same' when s.id is not null then 'name' when g.id is not null then 'area' else 'name' end as basis
          from this t
          left join lateral (
            select p.id, p.area, p.day, p.notional, not exists (
              select from area_geometry gp
              join area_geometry gn on gn.area_id = t.area and gn.valid @> t.drawn
              where gp.area_id = p.area and gp.valid @> p.drawn and gp.valid <> gn.valid
                and ST_Area(ST_Intersection(gp.geom, gn.geom, 0.00001)) < 0.97 * greatest(ST_Area(gp.geom), ST_Area(gn.geom))
            ) as same
            from prev p where p.area = t.area order by p.notional desc, p.drawn desc, p.day desc limit 1
          ) s on true
          left join lateral (
            select p.id, p.area, p.day, ST_Area(x.geom) >= 0.97 * greatest(ST_Area(gp.geom), ST_Area(gn.geom)) as same
            from prev p
            join area_geometry gp on gp.area_id = p.area and gp.valid @> p.drawn
            join area_geometry gn on gn.area_id = t.area and gn.valid @> t.drawn
            cross join lateral (select ST_Intersection(gp.geom, gn.geom, 0.00001) as geom) x
            where gp.geom && gn.geom and ST_Intersects(gp.geom, gn.geom)
              and (not t."isIe" or ST_Area(x.geom) > 0.5 * ST_Area(gn.geom))
            order by ST_Area(x.geom) desc, p.day desc limit 1
          ) g on s.id is null
          left join lateral (
            select p.id, p.area, p.day from prev p
            where exists (
              select from name a join name b on lower(a.text) = lower(b.text)
              where a.entity_id = t.area and b.entity_id = p.area
            ) order by p.day desc limit 1
          ) n on s.id is null and g.id is null
        ),
        top as materialized (
          select rs.contest_id as contest, rs.k as set, e.date_from as day, o.id, o.k, o.party_id, coalesce(x.seats, 1) as seats, v.votes
          from result_set rs
          join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          join contest_option o on o.id = x.option_id
          join election e on e.id = rs.election_id
          left join lateral (
            select max(y.value) as votes from contest kc join area ka on ka.id = kc.district_area_id
            cross join lateral (
              select y.value from result_rollup y
              where y.result_set_k = rs.k and y.area_k = ka.k and y.option_k = o.k and y.measure = 1 and y.count_round = 0 offset 0
            ) y
            where kc.id = rs.contest_id
          ) v on true
          where ${latest("rs")} and rs.contest_id in (select id from paired union select prior from paired)
          union all
          select rs.contest_id, rs.k, e.date_from, o.id, o.k, o.party_id, 1, o.votes
          from contest nc
          join area na on na.id = nc.district_area_id
          join election e on e.id = nc.election_id
          join result_set rs on rs.contest_id = nc.id and rs.is_default and rs.stage = 'notional'
          cross join lateral (
            select o.id, o.k, o.party_id, y.value as votes from contest_option o
            join result_rollup y on y.result_set_k = rs.k and y.area_k = na.k and y.option_k = o.k
              and y.measure = 1 and y.channel = 0 and y.count_round = 0
            where o.contest_id = nc.id
            order by y.value desc limit 1
          ) o
          where nc.notional and nc.id in (select prior from paired)
        ),
        won as (
          select distinct on (t.contest, t.k) t.contest, t.k, t.seats, t.votes,
            coalesce(bm.into_id, br.parent_party_id, lin.party, sp.party, pm.into_id, lp.party)::text as party,
            cy.person_id::text as person
          from top t
          cross join lateral (
            select coalesce(t.party_id, (
              select ch.party_id from contest_option ch
              join contest lc on lc.id = ch.contest_id join area la on la.id = lc.district_area_id
              join result_rollup y on y.result_set_k = t.set and y.area_k = la.k and y.option_k = ch.k
              where t.party_id is null and ch.parent_option_id = t.id and ch.party_id is not null
              order by y.value desc limit 1
            )) as party
          ) lp
          left join candidacy cy on cy.option_id = t.id and cy.role = 'head'
          left join entity_merge pm on pm.entity_id = lp.party
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, lp.party)
            and br.kind = 'regional_branch' and br.valid @> t.day
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          left join lateral (
            select coalesce(nm.into_id, d.new) as party from party_relation r
            cross join lateral (
              select case when r.kind = 'successor_of' then r.to_party_id else r.from_party_id end as old,
                case when r.kind = 'successor_of' then r.from_party_id else r.to_party_id end as new
            ) d
            left join entity_merge nm on nm.entity_id = d.new
            where t.contest in (select prior from paired)
              and r.kind in ('merged_into', 'absorbed_by', 'successor_of')
              and d.old = coalesce(pm.into_id, lp.party)
              and r.date > t.day and r.date <= (select day from this) + 365
              and d.old not in (select x.party_id from top x where x.contest in (select id from paired) and x.party_id is not null)
            limit 1
          ) lin on true
          left join lateral (
            select coalesce(xm.into_id, xo.party_id) as party
            from candidacy xc
            join contest_option xo on xo.id = xc.option_id
            join contest xk on xk.id = xo.contest_id
            join election xe on xe.id = xk.election_id
            left join entity_merge xm on xm.entity_id = xo.party_id
            join party xp on xp.id = coalesce(xm.into_id, xo.party_id)
            where t.contest in (select prior from paired)
              and exists (
                select from party sx join name sn on sn.entity_id = sx.id and sn.text = 'Speaker'
                where sx.kind = 'independent_group' and sx.id = coalesce(pm.into_id, lp.party)
              )
              and xc.person_id = cy.person_id and xe.date_from < t.day
              and not exists (
                select from top n join candidacy nc on nc.option_id = n.id and nc.role = 'head'
                where n.contest in (select id from paired) and nc.person_id = cy.person_id
              )
              and not (xp.kind = 'independent_group' and exists (select from name sn where sn.entity_id = xp.id and sn.text = 'Speaker'))
            order by xe.date_from desc limit 1
          ) sp on true
          order by t.contest, t.k
        )
        select p.basis, p.prior, p.area, p.day::text as since, (select day::text from this) as day, (select "isUs" from this) as "isUs", (select "isIe" from this) as "isIe",
          array(select coalesce(x.party, 'person:' || coalesce(x.person, 'k' || x.k)) from won x cross join generate_series(1, x.seats) where x.contest = p.id order by x.k) as now,
          array(
            select coalesce(x.party, 'person:' || coalesce(x.person, 'k' || x.k)) from won x cross join generate_series(1, x.seats) where x.contest = p.prior
            order by case when pc.magnitude > (select magnitude from this) then x.votes end desc nulls last, x.k
            limit case when pc.magnitude > (select magnitude from this) then (select magnitude from this) end
          ) as before,
          (select n.text from alliance a
            join alliance_member x on x.alliance_id = a.id and x.party_id::text = w.party
            join alliance_member y on y.alliance_id = a.id and y.party_id::text = b.party
            join name n on n.entity_id = a.id and n.is_preferred
            where w.party <> b.party and a.election_id in (
              (select election from last),
              (select coalesce(e.parent_id, e.id) from contest c join election e on e.id = c.election_id where c.id = p.id),
              (select coalesce(e.parent_id, e.id) from contest c join election e on e.id = c.election_id where c.id = p.prior)
            ) order by n.recorded_from desc, n.id limit 1) as alliance,
          v.person as member, v.via,
          (w.party is null or exists (select from party x where x.id::text = w.party and x.kind = 'independent_group'))
            and (b.party is null or exists (select from party x where x.id::text = b.party and x.kind = 'independent_group')) as "isOther"
        from paired p
        join contest pc on pc.id = p.prior
        left join lateral (
          select coalesce(
            (select coalesce(m.into_id, t.person_id)::text from tenure t left join entity_merge m on m.entity_id = t.person_id
              where t.body_id = (select body from this) and t.to_date between p.day and (select day from this)
                and (t.contest_id = p.prior or t.person_id::text in (select x.person from won x where x.contest = p.prior))
              order by t.to_date desc limit 1),
            (select max(x.person) from won x where x.contest = p.prior having count(*) = 1)
          ) as person,
          (select t.ended_via::text from tenure t
            where t.body_id = (select body from this) and t.to_date between p.day and (select day from this)
              and (t.contest_id = p.prior or t.person_id::text in (select x.person from won x where x.contest = p.prior))
            order by t.to_date desc limit 1) as via
          where (select kind from this) = 'by_election'
        ) v on true
        left join lateral (select x.party from won x where x.contest = p.id order by x.k limit 1) w on true
        left join lateral (select x.party from won x where x.contest = p.prior order by x.k limit 1) b on true
        where p.prior is not null
      `);
      const row = found.rows[0];
      const [now, before] = [row?.now[0], row?.before[0]];
      if (
        row == null ||
        now == null ||
        before == null ||
        row.now.length !== row.before.length
      ) {
        return null;
      }
      const [current, colored, labels, slugs] = await Promise.all([
        refs(
          row.now.filter((key) => !key.startsWith("person:")),
          row.day,
        ),
        refs(
          row.before.filter((key) => !key.startsWith("person:")),
          row.since,
        ),
        names(
          row.member == null ? [row.area] : [row.area, row.member],
          row.since,
        ),
        paths([row.prior]),
      ]);
      const party = (key: string, isBefore = false) => {
        const ref = isBefore
          ? (colored.get(key) ?? current.get(key))
          : (current.get(key) ?? colored.get(key));
        return key === "independent" || key.startsWith("person:")
          ? null
          : { label: ref?.label ?? "", short: ref?.short ?? null };
      };
      const kept = [...row.before];
      const held: Array<string> = [];
      const gained: Array<string> = [];
      for (const key of row.now) {
        const at = kept.indexOf(key);
        if (at === -1) {
          gained.push(key);
        } else {
          held.push(key);
          kept.splice(at, 1);
        }
      }
      return {
        since: Number(row.since.slice(0, 4)),
        isGain: row.alliance == null && gained.length > 0 && !row.isOther,
        isOther:
          row.isOther &&
          (party(now)?.label ?? null) !== (party(before, true)?.label ?? null),
        lead: row.isUs ? "margin" : "majority",
        basis: row.basis,
        party: party(now),
        from: party(before, true),
        prior: {
          name: labels.full.get(row.area) ?? "",
          path: slugs.get(row.prior) ?? null,
        },
        alliance:
          row.alliance === current.get(now)?.label ? null : row.alliance,
        vacated:
          row.member == null
            ? null
            : { name: labels.full.get(row.member) ?? "", via: row.via },
        seats:
          row.now.length < 2
            ? undefined
            : row.isIe ||
                [...row.now, ...row.before].every(
                  (key) => !key.startsWith("person:"),
                )
              ? [
                  ...new Set(
                    [...row.now, ...row.before].map((key) => fold(key)),
                  ),
                ]
                  .map((key) => {
                    const seats = row.now.filter(
                      (entry) => fold(entry) === key,
                    ).length;
                    const change =
                      seats -
                      row.before.filter((entry) => fold(entry) === key).length;
                    return {
                      isGain: change > 0,
                      count: seats,
                      change,
                      party: party(key),
                      from: null,
                    };
                  })
                  .toSorted(
                    (a, b) =>
                      b.count - a.count ||
                      Math.abs(b.change) - Math.abs(a.change),
                  )
              : Map.groupBy(
                  [
                    ...gained.map((key, index) => ({
                      key,
                      from: kept[index] ?? null,
                    })),
                    ...held.map((key) => ({ key, from: null })),
                  ],
                  (seat) =>
                    `${fold(seat.key)}|${seat.from == null ? "" : fold(seat.from)}`,
                )
                  .values()
                  .map((group) => ({
                    isGain: group[0]?.from != null,
                    count: group.length,
                    party: party(group[0]?.key ?? ""),
                    from:
                      group[0]?.from == null
                        ? null
                        : party(group[0].from, true),
                  }))
                  .toArray(),
      };
    }),
  byElections: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/by-elections",
        summary:
          "List the by-elections held in the parliament a general election chose, with each seat's previous holder",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.nullable(
        v.object({
          until: v.nullable(v.number()),
          general: v.nullable(
            v.object({ date: v.string(), path: v.nullable(v.string()) }),
          ),
          parties: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
            }),
          ),
          rows: v.array(
            v.object({
              date: v.string(),
              name: v.string(),
              path: v.nullable(v.string()),
              to: v.nullable(v.string()),
              from: v.nullable(v.string()),
              majority: v.nullable(v.number()),
              winner: v.nullable(
                v.object({ name: v.string(), path: v.nullable(v.string()) }),
              ),
              member: v.nullable(
                v.object({ name: v.string(), path: v.nullable(v.string()) }),
              ),
              reason: v.nullable(v.string()),
              cause: v.nullable(v.string()),
              isOther: v.boolean(),
              isHere: v.boolean(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        area: string | null;
        day: string;
        until: string | null;
        now: string | null;
        winner: string | null;
        before: string | null;
        member: string | null;
        via: string | null;
        cause: string | null;
        isOther: boolean;
        majority: number | null;
        general: string;
        since: string;
        isHere: boolean;
      }>(sql`
        with recursive tree(id) as (
          select ${input.id}::uuid
          union all
          select e.id from election e join tree on e.parent_id = tree.id where e.kind not in ('runoff', 'indirect')
        ),
        held as materialized (
          select c.body_id as body, min(e.date_from) as day
          from contest c join tree on tree.id = c.election_id join election e on e.id = c.election_id
          where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
            and c.district_area_id is not null and c.body_id is not null and not c.notional
          group by c.body_id order by count(*) desc limit 1
        ),
        general as (
          select e.date_from as day, coalesce(e.parent_id, e.id) as id
          from held h join contest c on c.body_id = h.body join election e on e.id = c.election_id
          where e.kind = 'general' and e.status = 'held' and e.date_from <= h.day
          order by e.date_from desc limit 1
        ),
        next as (
          select min(e.date_from) as day
          from contest c join election e on e.id = c.election_id, held h, general g
          where c.body_id = h.body and e.kind = 'general' and e.status = 'held' and e.date_from > g.day + 120
        ),
        bys as materialized (
          select c.id, c.district_area_id as area, e.date_from as day, h.body, c.election_id in (select id from tree) as "isHere"
          from held h join contest c on c.body_id = h.body join election e on e.id = c.election_id
          where e.kind = 'by_election' and e.date_from > (select day from general)
            and e.date_from < coalesce((select day from next), 'infinity')
            and c.kind = 'body_seats' and c.allocation_parent_id is null
            and ${input.within == null ? sql`true` : inside(input.within, sql`c.district_area_id`, sql`e.date_from`)}
        ),
        prior as materialized (
          select b.id, p.id as prior, p.day as since
          from bys b cross join lateral (
            select q.id, q.day from (
              select c.id, e.date_from as day, max(e.date_from) over () as newest
              from contest c join election e on e.id = c.election_id
              where c.body_id = b.body and c.kind = 'body_seats'
                and (c.district_area_id = b.area or (b.area is null and c.district_area_id is null and exists (
                  select from slug sc join slug sb on sb.entity_id = b.id and sb.is_current
                  where sc.entity_id = c.id and sc.is_current and substring(sc.path from '[^/]+$') = substring(sb.path from '[^/]+$')
                )))
                and c.allocation_parent_id is null and e.kind in ('general', 'by_election') and e.date_from < b.day and not c.notional
            ) q
            order by (
              select max(t.to_date) from tenure t
              left join entity_merge tm on tm.entity_id = t.person_id
              where t.body_id = b.body and t.to_date > q.newest and t.to_date <= b.day and q.day >= (select day from general)
                and (t.contest_id = q.id or coalesce(tm.into_id, t.person_id) in (
                  select coalesce(cm.into_id, cy.person_id) from result_set rs
                  join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  join candidacy cy on cy.option_id = x.option_id and cy.role = 'head'
                  left join entity_merge cm on cm.entity_id = cy.person_id
                  where rs.contest_id = q.id
                ))
            ) desc nulls last, q.day desc limit 1
          ) p
        ),
        won as materialized (
          select rs.contest_id as contest, o.k,
            coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id)::text as party,
            coalesce(mm.into_id, cy.person_id)::text as person
          from result_set rs
          join contest_option o on o.contest_id = rs.contest_id and exists (
            select from outcome x
            where x.result_set_id = rs.id and x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          )
          join election e on e.id = rs.election_id
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge mm on mm.entity_id = cy.person_id
          left join entity_merge pm on pm.entity_id = o.party_id
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
            and br.kind = 'regional_branch' and br.valid @> e.date_from
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          where ${latest("rs")} and rs.contest_id in (select id from bys union select prior from prior)
        ),
        left_seat as (
          select b.id, t.person, t.via
          from bys b join prior p on p.id = b.id
          cross join lateral (
            select coalesce(m.into_id, t.person_id)::text as person, t.ended_via::text as via
            from tenure t left join entity_merge m on m.entity_id = t.person_id
            where t.body_id = b.body and t.to_date between p.since and b.day
              and (t.contest_id = p.prior or coalesce(m.into_id, t.person_id)::text in (select w.person from won w where w.contest = p.prior))
            order by t.to_date desc limit 1
          ) t
        ),
        votes as (
          select b.id, sum(x.value)::float8 as votes, row_number() over (partition by b.id order by sum(x.value) desc) as place
          from bys b join result_set rs on rs.contest_id = b.id and ${latest("rs")}
          cross join lateral (
            select coalesce((select a.k from area a where a.id = b.area), (
              select y.area_k from result_rollup y where y.result_set_k = rs.k and y.measure = 1 and y.option_k <> 0
              group by y.area_k order by sum(y.value) desc, y.area_k limit 1
            )) as k
          ) a
          cross join lateral (
            select x.option_k, x.value from result_rollup x
            where x.result_set_k = rs.k and x.area_k = a.k and x.measure = 1 and x.option_k <> 0 offset 0
          ) x
          group by b.id, x.option_k
        )
        , listed as (
        select b.id, b.area, b.day::text as day, (select day::text from next) as until,
          (select id::text from general) as general, (select day::text from general) as since, b."isHere",
          (select coalesce(party, 'person:' || person) from won w where w.contest = b.id order by w.k limit 1) as now,
          (select person from won w where w.contest = b.id order by w.k limit 1) as winner,
          coalesce(
            (select coalesce(w.party, 'person:' || w.person) from won w join left_seat l on l.id = b.id and l.person = w.person where w.contest = p.prior limit 1),
            (select max(coalesce(w.party, 'person:' || w.person)) from won w where w.contest = p.prior having count(*) = 1),
            (select max(coalesce(w.party, 'person:' || w.person)) from won w
              join name wn on wn.entity_id = w.person::uuid and wn.is_preferred
              join name ln on ln.entity_id = l.person::uuid and ln.is_preferred
              where w.contest = p.prior and substring(wn.text from '[^ ]+$') = substring(ln.text from '[^ ]+$')
              having count(*) = 1)
          ) as before,
          coalesce(l.person, (select max(w.person) from won w where w.contest = p.prior having count(*) = 1)) as member,
          l.via,
          (select votes from votes v where v.id = b.id and v.place = 1) - (select votes from votes v where v.id = b.id and v.place = 2) as majority
        from bys b left join prior p on p.id = b.id left join left_seat l on l.id = b.id
        )
        select q.id, q.area, q.day, q.until, q.general, q.since, q."isHere", q.now, q.winner,
          coalesce(sp.party, q.before) as before, q.member, q.via, q.majority,
          (select n.body_md from note n where n.entity_id = q.id::uuid and n.kind = 'context' order by n.id limit 1) as cause,
          (q.now like 'person:%' or exists (select from party x where x.id::text = q.now and x.kind = 'independent_group'))
            and (coalesce(sp.party, q.before) like 'person:%' or exists (
              select from party x where x.id::text = coalesce(sp.party, q.before) and x.kind = 'independent_group'
            )) as "isOther"
        from listed q
        left join lateral (
          select coalesce(pm.into_id, co.party_id)::text as party
          from candidacy cy
          left join entity_merge cm on cm.entity_id = cy.person_id
          join contest_option co on co.id = cy.option_id
          join contest c on c.id = co.contest_id
          join election e on e.id = c.election_id
          left join entity_merge pm on pm.entity_id = co.party_id
          join party pp on pp.id = coalesce(pm.into_id, co.party_id)
          where exists (
              select from party sx join name sn on sn.entity_id = sx.id and sn.text = 'Speaker'
              where sx.kind = 'independent_group' and sx.id::text = q.before
            )
            and coalesce(cm.into_id, cy.person_id)::text = q.member
            and q.winner is distinct from q.member
            and e.date_from < q.day::date
            and not (pp.kind = 'independent_group' and exists (select from name sn where sn.entity_id = pp.id and sn.text = 'Speaker'))
          order by e.date_from desc limit 1
        ) sp on true
        order by q.day, q.id
      `);
      if (found.rows.length === 0) {
        return null;
      }
      const keys = [
        ...new Set(
          found.rows.flatMap((row) =>
            [row.now, row.before].filter((key) => key != null),
          ),
        ),
      ];
      const people = [
        ...new Set(
          found.rows.flatMap((row) =>
            [row.winner, row.member].filter((key) => key != null),
          ),
        ),
      ];
      const day = found.rows[0]?.day ?? null;
      const [colored, labels, slugs] = await Promise.all([
        refs(
          keys.filter((key) => !key.startsWith("person:")),
          day,
        ),
        names([...people, ...found.rows.map((row) => row.area ?? row.id)], day),
        paths([
          ...people,
          ...found.rows.map((row) => row.id),
          found.rows[0]?.general ?? "",
        ]),
      ]);
      const person = (id: string | null) =>
        id == null
          ? null
          : { name: labels.full.get(id) ?? "", path: slugs.get(id) ?? null };
      const first = found.rows[0];
      return {
        until: first?.until == null ? null : Number(first.until.slice(0, 4)),
        general:
          first == null || found.rows.every((row) => !row.isHere)
            ? null
            : { date: first.since, path: slugs.get(first.general) ?? null },
        parties: keys
          .toSorted((a, b) => a.localeCompare(b))
          .flatMap((key) => {
            const party = colored.get(key);
            return party == null
              ? []
              : [
                  {
                    key,
                    label: party.label,
                    short: party.short ?? null,
                    color: party.color ?? null,
                  },
                ];
          }),
        rows: found.rows.map((row) => ({
          date: row.day,
          name:
            row.area == null
              ? (labels.short.get(row.id) ?? labels.full.get(row.id) ?? "")
              : (labels.full.get(row.area) ?? ""),
          path: slugs.get(row.id) ?? null,
          to: row.now,
          from: row.before,
          majority: row.majority,
          winner: person(row.winner),
          member: person(row.member),
          reason: row.via,
          cause: row.via == null ? row.cause : null,
          isOther: row.isOther === true,
          isHere: row.isHere,
        })),
      };
    }),
  hexes: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/hexes",
        summary:
          "Place each single-member seat of an election on an equal-area hex layout, where one exists",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.nullable(
        v.object({
          credit: v.nullable(v.string()),
          cells: v.array(
            v.object({
              k: v.number(),
              q: v.number(),
              r: v.number(),
              isApart: v.boolean(),
            }),
          ),
          borders: v.array(
            v.object({
              isNation: v.boolean(),
              edges: v.array(v.array(v.number())),
            }),
          ),
          labels: v.array(
            v.object({ q: v.number(), r: v.number(), text: v.string() }),
          ),
          cartogram: v.nullable(
            v.object({
              credit: v.string(),
              box: v.array(v.number()),
              districts: v.array(
                v.object({
                  k: v.number(),
                  d: v.string(),
                  x: v.number(),
                  y: v.number(),
                }),
              ),
              states: v.array(v.string()),
              labels: v.array(
                v.object({ x: v.number(), y: v.number(), text: v.string() }),
              ),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const american = await unitedStates(input.id, input.within);
      if (american != null) {
        return american;
      }
      const found = await db.execute<{ k: number; code: string }>(sql`
        with recursive tree(id) as (
          select e.id from election e join slug ps on ps.entity_id = e.polity_id and ps.is_current and (ps.path = 'gb' or ps.path like 'gb/%') where e.id = ${input.id}::uuid
          union all
          select e.id from election e join tree on e.parent_id = tree.id where e.kind not in ('runoff', 'indirect')
        )
        select distinct a.k, ac.code
        from contest c join tree on tree.id = c.election_id
        join election e on e.id = c.election_id
        join area a on a.id = c.district_area_id
        join area_code ac on ac.area_id = a.id and (ac.valid @> e.date_from or ac.valid is null)
        where c.kind = 'body_seats' and c.allocation_parent_id is null and c.party_scope_id is null
          and coalesce(c.magnitude, 1) = 1 and not c.notional
          and ${input.within == null ? sql`true` : inside(input.within, sql`c.district_area_id`, sql`e.date_from`)}
      `);
      const layout = [layout2024, layout2010].find((entry) => {
        let matched = 0;
        for (const row of found.rows) {
          if (Object.hasOwn(entry.hexes, row.code)) {
            matched += 1;
          }
        }
        return matched > 0 && matched >= found.rows.length * 0.95;
      });
      if (layout == null) {
        return null;
      }
      const hexes: Record<string, Array<number>> = layout.hexes;
      const cells = found.rows.flatMap((row) => {
        const [q, r] = hexes[row.code] ?? [];
        return q == null || r == null
          ? []
          : [{ k: row.k, q, r, isApart: false }];
      });
      const kept = new Set(cells.map((cell) => `${cell.q},${cell.r}`));
      return {
        credit: "Open Innovations and contributors (MIT)",
        cells,
        borders: Object.entries(layout.boundaries).flatMap(([name, edges]) => {
          const isNation = ["sc-en", "wa-en"].includes(name);
          return isNation && input.within != null
            ? []
            : [
                {
                  isNation,
                  edges: edges.filter(([q, r]) => kept.has(`${q},${r}`)),
                },
              ];
        }),
        labels: [],
        cartogram: null,
      };
    }),
  additional: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/additional",
        summary:
          "Allocate a regional list's additional seats by D'Hondt against the constituency seats each party won in the region, seat by seat",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          seats: v.number(),
          isTopUp: v.boolean(),
          threshold: v.nullable(v.number()),
          isCounted: v.boolean(),
          isReproduced: v.boolean(),
          parties: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
              isParty: v.boolean(),
              votes: v.nullable(v.number()),
              constituencies: v.number(),
              declared: v.number(),
              computed: v.nullable(v.number()),
              quotients: v.array(v.nullable(v.number())),
            }),
          ),
          picks: v.array(v.string()),
          next: v.nullable(v.object({ key: v.string(), quotient: v.number() })),
          constituencies: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              key: v.string(),
              member: v.nullable(v.string()),
              memberPath: v.nullable(v.string()),
            }),
          ),
          members: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              key: v.string(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        tier: "list" | "seat";
        id: string;
        key: string;
        own: string | null;
        label: string;
        is_person: boolean;
        is_pool: boolean;
        votes: number | null;
        seats: number | null;
        person: string | null;
        ballot: string | null;
        magnitude: number;
        day: string;
        kind: string;
        threshold: number | null;
      }>(sql`
        with c as materialized (
          select c.id, c.election_id, c.district_area_id as region, a.k as region_k, c.magnitude, e.date_from as day, c.kind::text as kind,
            (coalesce(c.rules_override -> 'threshold', es.rules -> 'threshold') ->> 'percent')::float8 as threshold
          from contest c join election e on e.id = c.election_id join area a on a.id = c.district_area_id
          join electoral_system es on es.id = c.electoral_system_id
          where c.id = ${input.id}::uuid and coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula') = 'dhondt'
            and (c.kind = 'list_allocation' or (c.kind = 'body_seats' and c.magnitude > 1))
            and c.allocation_parent_id is null and c.magnitude is not null
        ),
        tiers as (
          select 'list' as tier, o.id, o.party_id, o.kind::text as kind, o.ballot_label,
            (select sum(y.value)::float8 from result_rollup y
              where y.result_set_k = rs.k and y.area_k = c.region_k and y.option_k = o.k
                and y.measure = 1 and y.channel = 0 and y.count_round = 0) as votes,
            (select max(coalesce(x.seats, 1))::int from outcome x
              where x.result_set_id = rs.id and x.option_id = o.id and x.candidacy_id is null and x.kind in ('won', 'elected')) as seats,
            null::uuid as person, null::text as ballot
          from c join result_set rs on rs.contest_id = c.id and ${latest("rs")}
          join contest_option o on o.contest_id = c.id
          union all
          select 'list', o.id, o.party_id, o.kind::text, o.ballot_label, null, null, cy.person_id, cy.ballot_name
          from c join contest_option o on o.contest_id = c.id
          join candidacy cy on cy.option_id = o.id and cy.role in ('list_member', 'head')
          join result_set rs on rs.contest_id = c.id and ${latest("rs")}
          where exists (select from outcome x where x.result_set_id = rs.id and x.option_id = o.id and x.kind in ('won', 'elected'))
            and (exists (select from outcome x where x.result_set_id = rs.id and x.candidacy_id = cy.id and x.kind in ('won', 'elected'))
              or not exists (select from outcome x where x.result_set_id = rs.id and x.candidacy_id is not null))
          union all
          select 'seat', k.id, o.party_id, o.kind::text, o.ballot_label, null, 1, cy.person_id, cy.ballot_name
          from c join contest k on k.election_id = c.election_id and k.kind = 'body_seats' and k.allocation_parent_id is null
          join area_parent ap on ap.area_id = k.district_area_id and ap.parent_id = c.region and ap.valid @> c.day
          join result_set rs on rs.contest_id = k.id and ${latest("rs")}
          join contest_option o on o.contest_id = k.id and exists (
            select from outcome x
            where x.result_set_id = rs.id and x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          )
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
        )
        select t.tier, t.id::text, coalesce(bm.into_id, br.parent_party_id, pm.into_id, t.party_id, t.id)::text as key,
          coalesce(pm.into_id, t.party_id)::text as own, t.ballot_label as label,
          t.party_id is null and t.kind <> 'list' as is_person, t.kind in ('others', 'independents') as is_pool, t.votes, t.seats, t.person::text, t.ballot,
          c.magnitude, c.day::text, c.kind, c.threshold
        from tiers t cross join c
        left join entity_merge pm on pm.entity_id = t.party_id
        left join party_affiliation br on br.party_id = coalesce(pm.into_id, t.party_id)
          and br.kind = 'regional_branch' and br.valid @> c.day
        left join entity_merge bm on bm.entity_id = br.parent_party_id
      `);
      const first = found.rows[0];
      const seated = found.rows.filter((row) => row.tier === "seat");
      if (
        first == null ||
        (seated.length === 0 && first.kind !== "body_seats")
      ) {
        return null;
      }
      const listed = found.rows.filter(
        (row) => row.tier === "list" && row.person == null && !row.is_pool,
      );
      const won = Map.groupBy(seated, (row) => row.key);
      const keys = [
        ...new Set([...listed.map((row) => row.key), ...won.keys()]),
      ];
      const own = new Map<string, string>();
      for (const row of [...seated, ...listed]) {
        if (row.own != null && !own.has(row.key)) {
          own.set(row.key, row.own);
        }
      }
      const people = new Set(found.rows.flatMap((row) => row.person ?? []))
        .values()
        .toArray();
      const [colored, labels, slugs] = await Promise.all([
        refs(own.values().toArray(), first.day),
        names([...people, ...seated.map((row) => row.id)], first.day),
        paths([...people, ...seated.map((row) => row.id)]),
      ]);
      const votes = new Map<string, number>();
      const declared = new Map<string, number>();
      for (const row of listed) {
        if (row.votes != null) {
          votes.set(row.key, (votes.get(row.key) ?? 0) + row.votes);
        }
        declared.set(row.key, (declared.get(row.key) ?? 0) + (row.seats ?? 0));
      }
      const isCounted = votes.size > 0;
      let valid = 0;
      for (const row of found.rows) {
        if (row.tier === "list" && row.person == null) {
          valid += row.votes ?? 0;
        }
      }
      const floor = ((first.threshold ?? 0) * valid) / 100;
      const solo = new Set(
        listed.filter((row) => row.is_person).map((row) => row.key),
      );
      const taken = new Map<string, number>();
      const quotients = new Map(
        keys.map((key) => [key, [] as Array<number | null>]),
      );
      const picks: Array<string> = [];
      let next: { key: string; quotient: number } | null = null;
      const last = isCounted ? first.magnitude : -1;
      for (let seat = 0; seat <= last; seat += 1) {
        let best: string | null = null;
        let top = 0;
        for (const key of keys) {
          const total = votes.get(key);
          const isOut = solo.has(key) && (taken.get(key) ?? 0) > 0;
          const quotient =
            total == null || isOut || total < floor
              ? null
              : total /
                ((won.get(key)?.length ?? 0) + (taken.get(key) ?? 0) + 1);
          quotients.get(key)?.push(quotient);
          if (quotient != null && quotient > top) {
            top = quotient;
            best = key;
          }
        }
        if (best == null) {
          break;
        }
        if (seat === first.magnitude) {
          next = { key: best, quotient: top };
          for (const list of quotients.values()) {
            list.pop();
          }
          break;
        }
        picks.push(best);
        taken.set(best, (taken.get(best) ?? 0) + 1);
      }
      const label = (key: string) => {
        const party = colored.get(own.get(key) ?? "");
        const row =
          listed.find((entry) => entry.key === key) ??
          seated.find((entry) => entry.key === key);
        const named = party?.label ?? "";
        return {
          label:
            named === ""
              ? ((row?.person == null
                  ? row?.label
                  : labels.full.get(row.person)) ?? "")
              : named,
          short: party?.short ?? null,
          color: party?.color ?? null,
        };
      };
      const parties = keys
        .map((key) => ({
          key,
          ...label(key),
          isParty: own.has(key),
          votes: votes.get(key) ?? null,
          constituencies: won.get(key)?.length ?? 0,
          declared: declared.get(key) ?? 0,
          computed: isCounted ? (taken.get(key) ?? 0) : null,
          quotients: quotients.get(key) ?? [],
        }))
        .toSorted(
          (a, b) =>
            b.constituencies + b.declared - (a.constituencies + a.declared) ||
            (b.votes ?? 0) - (a.votes ?? 0) ||
            a.key.localeCompare(b.key),
        );
      return {
        seats: first.magnitude,
        isTopUp: seated.length > 0,
        threshold: first.threshold,
        isCounted,
        isReproduced:
          isCounted &&
          parties.every((party) => party.computed === party.declared),
        parties,
        picks,
        next,
        constituencies: seated
          .map((row) => ({
            name: (labels.full.get(row.id) ?? "").replace(/^.+? in /u, ""),
            path: slugs.get(row.id) ?? null,
            key: row.key,
            member:
              row.person == null
                ? row.ballot
                : (labels.full.get(row.person) ?? row.ballot),
            memberPath:
              row.person == null ? null : (slugs.get(row.person) ?? null),
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
        members: found.rows
          .filter((row) => row.tier === "list" && row.person != null)
          .map((row) => ({
            name: labels.full.get(row.person ?? "") ?? row.ballot ?? "",
            path: slugs.get(row.person ?? "") ?? null,
            key: row.key,
          })),
      };
    }),
  successors: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/successors",
        summary:
          "Where a constituency's land went at the next boundary change, as shares of its area estimated from boundary maps, on the last general election held under its boundaries",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          year: v.string(),
          method: v.string(),
          places: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              share: v.number(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        vintage: string;
        method: string;
        area: string;
        share: number;
        contest: string | null;
        day: string;
      }>(sql`
        with c as materialized (
          select k.district_area_id as area, k.body_id as body, a.k, a.scheme_code, e.date_from as day
          from contest k join election e on e.id = k.election_id join area a on a.id = k.district_area_id
          where k.id = ${input.id}::uuid and k.kind = 'body_seats' and e.kind = 'general'
        ),
        s as materialized (
          select bs.id, bs.valid from boundary_set bs join c on bs.scheme_code = c.scheme_code and bs.valid @> c.day
          order by bs.valid_from desc nulls last limit 1
        )
        select t.vintage, x.method, ta.id::text as area, w.weight::float8 as share, c.day::text as day,
          (select k.id::text from contest k join election e on e.id = k.election_id
            where k.district_area_id = ta.id and k.body_id is not distinct from c.body and k.kind = 'body_seats' and e.kind = 'general' and e.status = 'held'
              and e.date_from >= lower(t.valid)
            order by e.date_from limit 1) as contest
        from s cross join c
        join crosswalk x on x.from_boundary_set_id = s.id and x.method in ('population', 'area')
        join boundary_set t on t.id = x.to_boundary_set_id and lower(t.valid) > lower(s.valid)
          and not exists (
            select from crosswalk y
            where y.from_boundary_set_id = x.from_boundary_set_id and y.to_boundary_set_id = x.to_boundary_set_id
              and y.method = 'population' and x.method <> 'population'
          )
        join crosswalk_weight w on w.crosswalk_id = x.id and w.from_k = c.k
        join area ta on ta.k = w.to_k
        where w.weight >= 0.01 and not exists (
          select from contest k join election e on e.id = k.election_id
          where k.district_area_id = c.area and k.body_id is not distinct from c.body and k.kind = 'body_seats' and e.kind = 'general' and e.status = 'held'
            and e.date_from > c.day and s.valid @> e.date_from
        )
        order by w.weight desc
      `);
      const first = found.rows[0];
      if (first == null) {
        return null;
      }
      const [labels, slugs] = await Promise.all([
        names(
          found.rows.map((row) => row.area),
          first.day,
        ),
        paths(found.rows.flatMap((row) => row.contest ?? [])),
      ]);
      return {
        year: first.vintage,
        method: first.method,
        places: found.rows.map((row) => ({
          name: labels.full.get(row.area) ?? "",
          path: slugs.get(row.contest ?? "") ?? null,
          share: Math.round(row.share * 100) / 100,
        })),
      };
    }),
  statutes: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/statutes",
        summary:
          "Seats a constituency lies within, or went into whole, at a boundary change, from links the redistribution Acts give in one direction only, on the first or last general election held under its boundaries",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.array(
        v.object({
          isNext: v.boolean(),
          year: v.string(),
          name: v.string(),
          path: v.nullable(v.string()),
          from: v.nullable(v.string()),
          to: v.nullable(v.string()),
          basis: v.nullable(v.string()),
          title: v.nullable(v.string()),
          url: v.nullable(v.string()),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        isNext: boolean;
        vintage: string;
        area: string;
        from: string | null;
        to: string | null;
        contest: string | null;
        basis: string | null;
        title: string | null;
        url: string | null;
      }>(sql`
        with c as materialized (
          select k.district_area_id as area, k.body_id as body, a.k, a.scheme_code, e.date_from as day
          from contest k join election e on e.id = k.election_id join area a on a.id = k.district_area_id
          where k.id = ${input.id}::uuid and k.kind = 'body_seats' and e.kind = 'general'
        ),
        s as materialized (
          select bs.id, bs.valid, bs.vintage from boundary_set bs join c on bs.scheme_code = c.scheme_code and bs.valid @> c.day
          order by bs.valid_from desc nulls last limit 1
        )
        select lower(t.valid) > lower(s.valid) as "isNext",
          case when lower(t.valid) > lower(s.valid) then t.vintage else s.vintage end as vintage, ta.id::text as area,
          extract(year from lower(t.valid))::text as from, extract(year from upper(t.valid))::text as to,
          (select k.id::text from contest k join election e on e.id = k.election_id
            where k.district_area_id = ta.id and k.body_id is not distinct from c.body and k.kind = 'body_seats' and e.kind = 'general' and e.status = 'held'
              and t.valid @> e.date_from
            order by case when lower(t.valid) > lower(s.valid) then e.date_from end,
              case when lower(t.valid) < lower(s.valid) then e.date_from end desc
            limit 1) as contest,
          ct.locator->>'basis' as basis, src.title, src.url
        from s cross join c
        join crosswalk x on x.from_boundary_set_id = s.id and x.method = 'exact'
        join boundary_set t on t.id = x.to_boundary_set_id
        join crosswalk y on y.from_boundary_set_id = x.to_boundary_set_id and y.to_boundary_set_id = x.from_boundary_set_id
          and y.method = 'exact'
        join crosswalk_weight w on w.crosswalk_id = x.id and w.from_k = c.k and w.exact
        join area ta on ta.k = w.to_k
        left join lateral (
          select ci.locator, ci.source_id from citation ci
          where ci.entity_id = c.area and ci.locator->>'crosswalk' = x.id::text
            and (ci.locator->>'to' = w.to_k::text
              or exists (select from area_code ac where ac.area_id = ta.id and ac.code = ci.locator->>'to'))
          limit 1
        ) ct on true
        left join source src on src.id = ct.source_id
        where not exists (
            select from crosswalk_weight v where v.crosswalk_id = y.id and v.from_k = w.to_k and v.to_k = w.from_k
          )
          and not exists (
            select from contest k join election e on e.id = k.election_id
            where k.district_area_id = c.area and k.body_id is not distinct from c.body and k.kind = 'body_seats' and e.kind = 'general' and e.status = 'held'
              and s.valid @> e.date_from
              and case when lower(t.valid) > lower(s.valid) then e.date_from > c.day else e.date_from < c.day end
          )
        order by 1, 2
      `);
      if (found.rows.length === 0) {
        return [];
      }
      const [labels, slugs] = await Promise.all([
        names(found.rows.map((row) => row.area)),
        paths(found.rows.flatMap((row) => row.contest ?? [])),
      ]);
      return found.rows.map((row) => ({
        isNext: row.isNext,
        year: row.vintage,
        name: labels.full.get(row.area) ?? "",
        path: slugs.get(row.contest ?? "") ?? null,
        from: row.from,
        to: row.to,
        basis: row.basis,
        title: row.title,
        url: row.url,
      }));
    }),
};
