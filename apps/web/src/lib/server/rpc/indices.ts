import type { Caveat, Entry, Unit } from "@ea/analytics/indices";
import { ancestry } from "@ea/analytics";
import {
  CAVEATS,
  effectiveParties,
  fragmentation,
  gallagher,
  loosemoreHanby,
  nationalization,
  pedersen,
} from "@ea/analytics/indices";
import { openapi } from "@orpc/openapi";
import { call } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os, remember } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { elections, polities } from "#lib/server/rpc/archive.js";

type Election = Awaited<ReturnType<typeof load>>;
type Race = Election["races"][number];
type Row = Race["results"][number];

type Chamber = {
  body: string;
  name: string;
  anchor: string;
  path: string | null;
  size: number;
  uncontested: number;
  entries: Array<Entry>;
  units: { noun: string; rows: Array<Unit> } | null;
  coalitions: Array<{ name: string; keys: Array<string> }>;
  caveats: Array<Caveat>;
};

const load = async (id: string) => call(elections.get, { id });

const isPooled = (row: Row) =>
  row.isIndependent === true ||
  row.key === "independent" ||
  row.key.startsWith("other") ||
  (row.party == null && row.person == null);

const fits = (seats: number, size: number) =>
  seats >= size * 0.95 && seats <= size * 1.02;

const seatsOf = (race: Race) => {
  let seats = 0;
  for (const row of race.results) {
    seats += row.seats ?? 0;
  }
  return seats;
};

function assemble(election: Election, body: string): Chamber | null {
  const races = election.races.filter(
    (race) =>
      race.body === body &&
      race.scope == null &&
      !race.isOffice &&
      race.size != null &&
      race.electors == null &&
      (race.kind === "body_seats" || race.kind === "list_allocation"),
  );
  const first = races.filter((race) => (race.round ?? 1) === 1);
  const size = first[0]?.size ?? 0;
  const caveats: Array<Caveat> = [];
  if (races.length > first.length) {
    caveats.push("first_round_votes");
  }
  const lists = first.filter((race) => race.kind === "list_allocation");
  const seated = first.filter((race) => race.kind === "body_seats");
  const whole = [...lists, ...seated].find((race) => fits(seatsOf(race), size));
  let combined = 0;
  for (const race of first) {
    combined += seatsOf(race);
  }
  const counted =
    whole ??
    (lists.length === 1 && seated.length > 0 && fits(combined, size)
      ? lists[0]
      : null);
  if (counted == null || size < 10) {
    return null;
  }
  if (counted.kind === "list_allocation" && seated.length > 0) {
    caveats.push("list_votes");
  }
  const tiers = whole == null ? first : [counted];
  const votes = new Map<string, Row>();
  for (const row of counted.results) {
    votes.set(row.key, row);
  }
  const seats = new Map<string, number>();
  let residual = false;
  for (const race of tiers) {
    for (const row of race.results) {
      let left = row.seats ?? 0;
      if (row.key.startsWith("alliance:")) {
        for (const credit of race.credits ?? []) {
          if (credit.alliance === row.label) {
            seats.set(credit.key, (seats.get(credit.key) ?? 0) + credit.seats);
            left -= credit.seats;
            if (!caveats.includes("alliance_seats_credited")) {
              caveats.push("alliance_seats_credited");
            }
          }
        }
        if (left > 0 && !votes.has(row.key)) {
          residual = true;
        }
      }
      if (left !== 0) {
        seats.set(row.key, (seats.get(row.key) ?? 0) + left);
      }
    }
  }
  const merge = new Map<string, { key: string; label: string }>();
  const places = counted.places;
  for (const coalition of election.coalitions) {
    const keys = coalition.parties.map((party) => `party:${party}`);
    const present = keys.filter((key) => votes.has(key) || seats.has(key));
    if (present.length < 2) {
      continue;
    }
    const columns = present.map(
      (key) => places?.legend.findIndex((item) => item.key === key) ?? -1,
    );
    const isSister =
      places != null &&
      columns.every((column) => column >= 0) &&
      places.rows.every(
        (row) =>
          columns.filter((column) => (row.shares[column] ?? 0) > 0).length <= 1,
      );
    const alliance = tiers
      .flatMap((race) => race.results)
      .find(
        (row) =>
          row.key.startsWith("alliance:") && row.label === coalition.name,
      );
    if (!isSister && (!residual || alliance == null)) {
      continue;
    }
    const target = isSister
      ? { key: present[0] ?? "", label: coalition.name }
      : { key: alliance?.key ?? "", label: coalition.name };
    for (const key of present) {
      merge.set(key, target);
    }
    const caveat = isSister ? "sisters_joined" : "alliances_as_units";
    if (!caveats.includes(caveat)) {
      caveats.push(caveat);
    }
  }
  const keyOf = (key: string) => merge.get(key)?.key ?? key;
  const built = new Map<string, Entry>();
  for (const key of new Set([...votes.keys(), ...seats.keys()])) {
    const row =
      votes.get(key) ??
      tiers
        .flatMap((race) => race.results)
        .find((candidate) => candidate.key === key);
    const target = keyOf(key);
    const entry = built.get(target) ?? {
      key: target,
      label: merge.get(key)?.label ?? row?.short ?? row?.label ?? key,
      votes: 0,
      seats: 0,
      isPooled: row == null ? false : isPooled(row),
    };
    entry.votes += votes.get(key)?.votes ?? 0;
    entry.seats += seats.get(key) ?? 0;
    built.set(target, entry);
  }
  let uncontested = 0;
  for (const race of tiers) {
    for (const party of race.unopposed?.parties ?? []) {
      const entry = built.get(keyOf(party.key));
      if (entry != null) {
        entry.seats = Math.max(entry.seats - party.seats, 0);
        uncontested += party.seats;
      }
    }
  }
  if (uncontested > 0) {
    caveats.push("uncontested_excluded");
  }
  const entries = built
    .values()
    .filter((entry) => entry.votes > 0 || entry.seats > 0)
    .toArray();
  let [partisan, total] = [0, 0];
  for (const entry of entries) {
    total += entry.seats;
    partisan += entry.isPooled ? 0 : entry.seats;
  }
  if (
    total < size * 0.5 - uncontested ||
    partisan < total * 0.5 ||
    entries.filter((entry) => entry.votes > 0).length < 2
  ) {
    return null;
  }
  const rows =
    places == null
      ? []
      : places.rows.flatMap((row) => {
          if (row.valid <= 0) {
            return [];
          }
          const tally: Record<string, number> = {};
          for (const [index, item] of places.legend.entries()) {
            const share = row.shares[index] ?? 0;
            if (share > 0) {
              const key = keyOf(item.key);
              tally[key] = (tally[key] ?? 0) + share * row.valid;
            }
          }
          return [{ valid: row.valid, votes: tally }];
        });
  return {
    body,
    name: counted.name,
    anchor: counted.anchor,
    path: counted.path ?? null,
    size,
    uncontested,
    entries,
    units:
      rows.length < 2
        ? null
        : {
            noun:
              places?.levels.find((level) => level.code === places.level)
                ?.noun ?? "",
            rows,
          },
    coalitions: election.coalitions.map((coalition) => ({
      name: coalition.name,
      keys: coalition.parties.map((party) => keyOf(`party:${party}`)),
    })),
    caveats,
  };
}

const chambers = async (id: string) =>
  remember<Array<Chamber>>(`indices.chambers:${id}`, async () => {
    const election = await load(id);
    const bodies = new Set(
      election.races.flatMap((race) =>
        race.body != null && race.scope == null ? [race.body] : [],
      ),
    );
    return bodies
      .values()
      .flatMap((body) => {
        const found = assemble(election, body);
        return found == null ? [] : [found];
      })
      .toArray();
  });

async function lineage(day: string, keys: Array<string>) {
  const ids = keys.flatMap((key) =>
    key.startsWith("party:") ? [key.slice(6)] : [],
  );
  const found = await db.execute<{ from: string; to: string; kind: string }>(
    sql`
      with recursive walk(id, depth) as (
        select unnest(${sql.param(ids)}::uuid[]), 0
        union
        select case when r.from_party_id = w.id then r.to_party_id else r.from_party_id end, w.depth + 1
        from walk w join party_relation r on (r.from_party_id = w.id or r.to_party_id = w.id)
        where w.depth < 4 and r.to_party_id is not null and (r.date is null or r.date <= ${day}::date)
      )
      select distinct coalesce(fm.into_id, r.from_party_id)::text as from, coalesce(tm.into_id, r.to_party_id)::text as to, r.kind::text
      from party_relation r
      left join entity_merge fm on fm.entity_id = r.from_party_id
      left join entity_merge tm on tm.entity_id = r.to_party_id
      where r.to_party_id is not null and (r.date is null or r.date <= ${day}::date)
        and r.from_party_id in (select id from walk)
      union
      select coalesce(fm.into_id, a.party_id)::text, coalesce(tm.into_id, a.parent_party_id)::text, a.kind::text
      from party_affiliation a
      left join entity_merge fm on fm.entity_id = a.party_id
      left join entity_merge tm on tm.entity_id = a.parent_party_id
      where a.kind = 'regional_branch' and a.valid @> ${day}::date
        and a.party_id = any(${sql.param(ids)}::uuid[])
    `,
  );
  const root = ancestry(
    found.rows.map((row) => ({ ...row, toLabel: "" })),
    "party",
  );
  return {
    group: (key: string) =>
      key.startsWith("party:") ? `party:${root(key.slice(6))}` : key,
    splits: new Set(
      found.rows
        .filter((row) => row.kind === "split_from")
        .map((row) => `party:${row.from}`),
    ),
  };
}

const round = (value: number, digits: number) =>
  Math.round(value * 10 ** digits) / 10 ** digits;

const figure = (
  found: { value: number; caveats: Array<Caveat> } | null,
  digits = 2,
) =>
  found == null
    ? null
    : { value: round(found.value, digits), caveats: found.caveats };

type Previous = {
  name: string;
  path: string | null;
  year: number;
  date: string;
  chamber: Chamber;
};

async function compute(
  chamber: Chamber,
  date: string,
  previous: Previous | null,
) {
  const moved =
    previous == null
      ? null
      : await (async () => {
          const { group, splits } = await lineage(date, [
            ...chamber.entries.map((entry) => entry.key),
            ...previous.chamber.entries.map((entry) => entry.key),
          ]);
          const named = (entries: Array<Entry>) =>
            new Set(
              entries
                .filter((entry) => entry.key.startsWith("alliance:"))
                .map((entry) => entry.label),
            );
          const folds = new Map<string, string>();
          for (const [own, other] of [
            [chamber, previous.chamber],
            [previous.chamber, chamber],
          ] as const) {
            const names = named(other.entries);
            for (const coalition of own.coalitions) {
              if (names.has(coalition.name)) {
                for (const key of coalition.keys) {
                  folds.set(key, coalition.name);
                }
              }
            }
          }
          const regroup = (key: string, label: string) => {
            const folded = folds.get(key);
            return folded != null
              ? `alliance:${folded}`
              : key.startsWith("alliance:")
                ? `alliance:${label}`
                : group(key);
          };
          const relabel = (entries: Array<Entry>) =>
            entries.map((entry) => ({
              ...entry,
              key: regroup(entry.key, entry.label),
              label: folds.get(entry.key) ?? entry.label,
            }));
          const before = new Set(
            previous.chamber.entries.map((entry) =>
              regroup(entry.key, entry.label),
            ),
          );
          const found = pedersen(
            relabel(chamber.entries),
            relabel(previous.chamber.entries),
            (key) => key,
            new Set(
              splits
                .values()
                .map((key) => group(key))
                .filter((key) => !before.has(key)),
            ),
          );
          if (found != null && folds.size > 0) {
            found.caveats.push("alliances_as_units");
          }
          return found;
        })();
  const national =
    chamber.units == null
      ? null
      : nationalization(chamber.units.rows, chamber.entries);
  let [votes, seats] = [0, 0];
  for (const entry of chamber.entries) {
    votes += entry.votes;
    seats += entry.seats;
  }
  return {
    body: chamber.body,
    name: chamber.name,
    anchor: chamber.anchor,
    path: chamber.path,
    basis: {
      votes,
      seats,
      size: chamber.size,
      uncontested: chamber.uncontested,
      parties: chamber.entries.filter((entry) => !entry.isPooled).length,
    },
    caveats: chamber.caveats,
    effectiveVotes: figure(effectiveParties(chamber.entries, "votes")),
    effectiveSeats: figure(effectiveParties(chamber.entries, "seats")),
    fragmentationVotes: figure(fragmentation(chamber.entries, "votes"), 3),
    fragmentationSeats: figure(fragmentation(chamber.entries, "seats"), 3),
    gallagher: figure(gallagher(chamber.entries), 1),
    loosemoreHanby: figure(loosemoreHanby(chamber.entries), 1),
    volatility:
      moved == null || previous == null
        ? null
        : {
            value: round(moved.value, 1),
            caveats: moved.caveats,
            previous: {
              name: previous.name,
              path: previous.path,
              year: previous.year,
            },
            moved: moved.inputs.moved
              .slice(0, 8)
              .map((row) => ({
                labels: row.labels,
                now: round(row.now, 4),
                before: round(row.before, 4),
              })),
          },
    nationalization:
      national == null || chamber.units == null
        ? null
        : {
            value: round(national.value, 2),
            caveats: national.caveats,
            noun: chamber.units.noun,
            units: national.inputs.units,
            parties: national.inputs.parties
              .slice(0, 8)
              .map((party) => ({
                key: party.key,
                label: party.label,
                share: round(party.share, 4),
                score: round(party.score, 2),
              })),
          },
  };
}

const Figure = v.nullable(
  v.object({ value: v.number(), caveats: v.array(v.picklist(CAVEATS)) }),
);

const Indices = v.object({
  body: v.string(),
  name: v.string(),
  anchor: v.string(),
  path: v.nullable(v.string()),
  basis: v.object({
    votes: v.number(),
    seats: v.number(),
    size: v.number(),
    uncontested: v.number(),
    parties: v.number(),
  }),
  caveats: v.array(v.picklist(CAVEATS)),
  effectiveVotes: Figure,
  effectiveSeats: Figure,
  fragmentationVotes: Figure,
  fragmentationSeats: Figure,
  gallagher: Figure,
  loosemoreHanby: Figure,
  volatility: v.nullable(
    v.object({
      value: v.number(),
      caveats: v.array(v.picklist(CAVEATS)),
      previous: v.object({
        name: v.string(),
        path: v.nullable(v.string()),
        year: v.number(),
      }),
      moved: v.array(
        v.object({
          labels: v.array(v.string()),
          now: v.number(),
          before: v.number(),
        }),
      ),
    }),
  ),
  nationalization: v.nullable(
    v.object({
      value: v.number(),
      caveats: v.array(v.picklist(CAVEATS)),
      noun: v.string(),
      units: v.number(),
      parties: v.array(
        v.object({
          key: v.string(),
          label: v.string(),
          share: v.number(),
          score: v.number(),
        }),
      ),
    }),
  ),
});

async function ids(paths: Array<string>) {
  const found = await db.execute<{ path: string; id: string }>(sql`
    select distinct on (s.path) s.path, s.entity_id::text as id from slug s join entity e on e.id = s.entity_id
    where s.path = any(${sql.param(paths)}::text[]) and e.type = 'election'
    order by s.path, s.is_current desc
  `);
  return new Map(found.rows.map((row) => [row.path, row.id]));
}

type Point = { name: string; path: string | null; date: string; year: number };

async function series(polity: string) {
  const record = await call(polities.record, { id: polity });
  return record.series.filter(
    (entry) =>
      !entry.isOffice &&
      !entry.isAbroad &&
      entry.isOwn &&
      entry.points.some((point) => point.filled >= 10),
  );
}

async function pool<T, R>(
  items: Array<T>,
  limit: number,
  run: (item: T) => Promise<R>,
) {
  const results: Array<R> = [];
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next++;
      const item = items[index];
      if (item !== undefined) {
        results[index] = await run(item);
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker),
  );
  return results;
}

async function measure(id: string) {
  const found = await db.execute<{ polity: string; date: string }>(sql`
    select polity_id::text as polity, date_from::text as date from election where id = ${id}::uuid
  `);
  const head = found.rows[0];
  const own = await chambers(id);
  if (head == null || own.length === 0) {
    return { chambers: [] };
  }
  const record = await series(head.polity);
  const paths = await ids(
    record.flatMap((entry) =>
      entry.points.flatMap((point) => point.path ?? []),
    ),
  );
  return {
    chambers: await Promise.all(
      own.map(async (chamber) => {
        const points = (
          record.find((entry) => entry.id === chamber.body)?.points ?? []
        ).filter((point) => point.date < head.date);
        let previous: Previous | null = null;
        for (const point of points.toReversed()) {
          const before = paths.get(point.path ?? "");
          if (before == null) {
            continue;
          }
          const match = (await chambers(before)).find(
            (entry) => entry.body === chamber.body,
          );
          if (match != null) {
            previous = { ...point, chamber: match };
          }
          break;
        }
        return compute(chamber, head.date, previous);
      }),
    ),
  };
}

export const indices = {
  election: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/indices",
        summary:
          "Compute political science indices for each chamber an election filled by party: effective number of parties on votes and seats, Rae fragmentation, Gallagher and Loosemore–Hanby disproportionality, Pedersen volatility against the chamber's previous general election, and the Jones–Mainwaring party system nationalization score, each with its caveats",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid(), v.toLowerCase()) }))
    .output(v.object({ chambers: v.array(Indices) }))
    .handler(async ({ input }) => measure(input.id)),
  contest: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/indices",
        summary:
          "Compute the political science indices of the chamber a list contest fills, when the contest is the one whose votes and seats the chamber's figures count",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid(), v.toLowerCase()) }))
    .output(v.object({ chamber: v.nullable(Indices) }))
    .handler(async ({ input }) => {
      const found = await db.execute<{ election: string; path: string }>(sql`
        select c.election_id::text as election, s.path from contest c
        join slug s on s.entity_id = c.id and s.is_current
        where c.id = ${input.id}::uuid and c.kind = 'list_allocation'
      `);
      const head = found.rows[0];
      if (head == null) {
        return { chamber: null };
      }
      const { chambers: measured } = await measure(head.election);
      return {
        chamber: measured.find((chamber) => chamber.path === head.path) ?? null,
      };
    }),
  polity: os
    .meta(
      openapi({
        method: "GET",
        path: "/polities/{id}/indices",
        summary:
          "List the political science indices of every general election of a polity's own chambers, oldest first: effective number of parties, Gallagher and Loosemore–Hanby disproportionality, Pedersen volatility, and nationalization",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid(), v.toLowerCase()) }))
    .output(
      v.object({
        series: v.array(
          v.object({
            body: v.string(),
            name: v.string(),
            points: v.array(
              v.object({
                name: v.string(),
                path: v.nullable(v.string()),
                date: v.string(),
                year: v.number(),
                effectiveVotes: v.nullable(v.number()),
                effectiveSeats: v.nullable(v.number()),
                gallagher: v.nullable(v.number()),
                loosemoreHanby: v.nullable(v.number()),
                volatility: v.nullable(v.number()),
                nationalization: v.nullable(v.number()),
                caveats: v.array(v.picklist(CAVEATS)),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const record = await series(input.id);
      const paths = await ids(
        record.flatMap((entry) =>
          entry.points.flatMap((point) => point.path ?? []),
        ),
      );
      const built = await Promise.all(
        record.map(async (entry) => {
          const loaded = await pool(entry.points, 4, async (point) => {
            const id = paths.get(point.path ?? "");
            const chamber =
              id == null
                ? null
                : ((await chambers(id)).find(
                    (found) => found.body === entry.id,
                  ) ?? null);
            return { point, chamber };
          });
          const points: Array<{ point: Point; chamber: Chamber }> =
            loaded.flatMap(({ point, chamber }) =>
              chamber == null ? [] : [{ point, chamber }],
            );
          const computed = await pool(
            points.map((item, index) => ({ item, index })),
            4,
            async ({ item, index }) => {
              const before = points[index - 1];
              const found = await compute(
                item.chamber,
                item.point.date,
                before == null
                  ? null
                  : { ...before.point, chamber: before.chamber },
              );
              return {
                name: item.point.name,
                path: item.point.path,
                date: item.point.date,
                year: item.point.year,
                effectiveVotes: found.effectiveVotes?.value ?? null,
                effectiveSeats: found.effectiveSeats?.value ?? null,
                gallagher: found.gallagher?.value ?? null,
                loosemoreHanby: found.loosemoreHanby?.value ?? null,
                volatility: found.volatility?.value ?? null,
                nationalization: found.nationalization?.value ?? null,
                caveats: [
                  ...new Set([
                    ...found.caveats,
                    ...(found.volatility?.caveats ?? []),
                  ]),
                ],
              };
            },
          );
          return { body: entry.id, name: entry.name, points: computed };
        }),
      );
      return { series: built.filter((entry) => entry.points.length > 1) };
    }),
};
