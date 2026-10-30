import type { SQL } from "drizzle-orm";
import { dev } from "$app/env";
import { openapi } from "@orpc/openapi";
import { os as base, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os, strict } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { plane } from "#lib/server/plane.js";
import { names, UNAFFILIATED } from "#lib/server/rpc/archive.js";
import { landing, NIGHT, rehearsal } from "#lib/server/rpc/rehearsal.js";

const LEVELS = new Set([
  "us.place",
  "us.state",
  "us.county",
  "us.nyc_ad",
  "us.nyc_ed",
]);
const TERRITORIES = new Set(["as", "gu", "mp", "pr", "vi"]);
const WHOLE = ["us.place", "us.state", "us.county"];
const ZONES: Record<string, string> = {
  ct: "America/New_York",
  de: "America/New_York",
  dc: "America/New_York",
  fl: "America/New_York",
  ga: "America/New_York",
  in: "America/New_York",
  ky: "America/New_York",
  me: "America/New_York",
  md: "America/New_York",
  ma: "America/New_York",
  mi: "America/New_York",
  nh: "America/New_York",
  nj: "America/New_York",
  ny: "America/New_York",
  nc: "America/New_York",
  oh: "America/New_York",
  pa: "America/New_York",
  ri: "America/New_York",
  sc: "America/New_York",
  vt: "America/New_York",
  va: "America/New_York",
  wv: "America/New_York",
  al: "America/Chicago",
  ar: "America/Chicago",
  il: "America/Chicago",
  ia: "America/Chicago",
  ks: "America/Chicago",
  la: "America/Chicago",
  mn: "America/Chicago",
  ms: "America/Chicago",
  mo: "America/Chicago",
  ne: "America/Chicago",
  nd: "America/Chicago",
  ok: "America/Chicago",
  sd: "America/Chicago",
  tn: "America/Chicago",
  tx: "America/Chicago",
  wi: "America/Chicago",
  co: "America/Denver",
  id: "America/Denver",
  mt: "America/Denver",
  nm: "America/Denver",
  ut: "America/Denver",
  wy: "America/Denver",
  az: "America/Phoenix",
  ca: "America/Los_Angeles",
  nv: "America/Los_Angeles",
  or: "America/Los_Angeles",
  wa: "America/Los_Angeles",
  ak: "America/Anchorage",
  hi: "Pacific/Honolulu",
  pr: "America/Puerto_Rico",
  vi: "America/St_Thomas",
  gu: "Pacific/Guam",
  mp: "Pacific/Saipan",
  as: "Pacific/Pago_Pago",
};

const color = (party: SQL, date: SQL) => sql`
  coalesce(
    (select pc.color from party_color pc where pc.party_id = ${party} and pc.valid @> ${date}),
    (select pc.color from party_affiliation a join party_color pc on pc.party_id = a.parent_party_id and pc.valid @> ${date}
      where a.party_id = ${party} and a.kind = 'regional_branch' and a.valid @> ${date} limit 1)
  )
`;

function group(path: string) {
  const seat = path.split("/").at(-1) ?? "";
  if (
    /^(?:mayor|public-advocate|comptroller|controller|governor|lieutenant-governor|attorney-general|secretary-of-state|treasurer|auditor|superintendent|commissioner)/u.test(
      seat,
    )
  ) {
    return "executive";
  }
  if (/^(?:senate|house)(?:-(?:\d+|at-large|special))*$/u.test(seat)) {
    return "federal";
  }
  if (seat.startsWith("borough-president")) {
    return "borough";
  }
  if (seat.startsWith("district-attorney")) {
    return "prosecutor";
  }
  if (seat.startsWith("council-district")) {
    return "council";
  }
  if (
    /^(?:proposal|proposition|question|measure|(?:proposed-)?(?:constitutional-)?amendment|(?:statewide-)?referendum|ballot-question|initiative|issue)/u.test(
      seat,
    )
  ) {
    return "measure";
  }
  if (/court|surrogate|justice|judge|chancellor/u.test(seat)) {
    return "judicial";
  }
  if (
    /^(?:state-house|state-senate|assembly|house-of-delegates|legislature)/u.test(
      seat,
    )
  ) {
    return "legislative";
  }
  return "other";
}

async function election(path: string) {
  const segments = path.split("/").filter((part) => part !== "");
  const found = await db.execute<{
    id: string;
    path: string;
    date: string;
    polity: string;
  }>(sql`
    select e.id, s.path, e.date_from::text as date, e.polity_id as polity
    from slug s join election e on e.id = s.entity_id
    where s.is_current and s.path = any(${sql.param(segments.map((_, index) => segments.slice(0, index + 1).join("/")))}::text[])
    order by length(s.path) desc limit 1
  `);
  const row = found.rows[0];
  if (row == null) {
    throw new ORPCError("NOT_FOUND");
  }
  return { ...row, rest: segments.slice(row.path.split("/").length).join("/") };
}

async function sets(id: string) {
  const found = await db.execute<{
    k: number;
    contest: string;
    at: string;
    outside: boolean;
    scheme: string;
    note: string | null;
    publisher: string;
    url: string | null;
    reported: number | null;
  }>(sql`
    select rs.k, rs.completeness::float8 as reported, rs.contest_id as contest, to_char(rs.as_of at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at, c.election_id <> ${id}::uuid as outside,
      (select u.scheme_code from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k limit 1) as scheme,
      (select n.body_md from note n where n.entity_id = rs.id and n.kind = 'methodology' limit 1) as note,
      s.publisher, s.url
    from contest c join result_set rs on rs.contest_id = c.id and rs.stage in ('live', 'preliminary') and rs.status = 'active'
    join source s on s.id = rs.source_id
    join election e on e.id = c.election_id
    where rs.as_of is not null and e.date_from = (select date_from from election where id = ${id}::uuid)
      and s.publisher in (
        select z.publisher from result_set x join contest y on y.id = x.contest_id join source z on z.id = x.source_id
        where y.election_id = ${id}::uuid and x.stage in ('live', 'preliminary') and x.as_of is not null
      )
  `);
  return found.rows.filter((row) => LEVELS.has(row.scheme));
}

async function totals(ks: Array<number>) {
  const found = await db.execute<{
    set: number;
    option: string;
    votes: number;
  }>(sql`
    select v.result_set_k as set, coalesce(o.parent_option_id, o.id) as option, sum(v.value)::float8 as votes
    from result_value v join contest_option o on o.k = v.option_k
    where v.result_set_k = any(${sql.param(ks)}::int[]) and v.measure = 1
    group by 1, 2
  `);
  return found.rows;
}

async function certified(contests: Array<string>) {
  const found = await db.execute<{
    contest: string;
    k: number;
    option: string;
    votes: number;
    elected: boolean;
  }>(sql`
    with picked as (
      select distinct on (rs.contest_id) rs.contest_id, rs.k, rs.id from result_set rs
      where rs.contest_id = any(${sql.param(contests)}::uuid[]) and rs.stage in ('official', 'certified')
        and rs.status = 'active' and rs.kind = 'counted'
      order by rs.contest_id, rs.is_default desc, rs.stage = 'certified' desc
    )
    select p.contest_id as contest, p.k, t.option, t.votes,
      exists (select from outcome x where x.result_set_id = p.id and x.option_id = t.option and x.kind in ('elected', 'won')) as elected
    from picked p cross join lateral (
      select coalesce(o.parent_option_id, o.id) as option, sum(v.value)::float8 as votes
      from result_value v join contest_option o on o.k = v.option_k
      where v.result_set_k = p.k and v.measure = 1
      group by 1
    ) t
  `);
  return found.rows;
}

async function options(contests: Array<string>, date: string) {
  const found = await db.execute<{
    id: string;
    contest: string;
    kind: string;
    label: string | null;
    person: string | null;
    ballot: string | null;
    party: string | null;
    color: string | null;
  }>(sql`
    select o.id, o.contest_id as contest, o.kind::text, o.ballot_label as label, ca.person_id as person, ca.ballot_name as ballot,
      line.party, ${color(sql`line.party`, sql`${date}::date`)} as color
    from contest_option o left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
    left join lateral (
      select x.party_id as party from contest_option x
      where (x.id = o.id or x.parent_option_id = o.id) and x.party_id is not null
      order by (
        select max(v.value) from result_set rs join result_value v on v.result_set_k = rs.k and v.option_k = x.k
        where rs.contest_id = x.contest_id
      ) desc nulls last,
        exists (select from party_color pc where pc.party_id = x.party_id) desc, x.k
      limit 1
    ) line on true
    where o.contest_id = any(${sql.param(contests)}::uuid[]) and o.parent_option_id is null
  `);
  const people = found.rows.flatMap((row) =>
    row.person == null ? [] : [row.person],
  );
  const parties = found.rows.flatMap((row) =>
    row.party == null ? [] : [row.party],
  );
  const [named, partyNames] = await Promise.all([
    names(people, date),
    names(parties, date),
  ]);
  return found.rows.map((row) => ({
    id: row.id,
    contest: row.contest,
    kind: row.kind,
    name:
      (row.person == null ? null : named.full.get(row.person)) ??
      row.ballot ??
      row.label ??
      "",
    party:
      row.party == null
        ? null
        : (partyNames.short.get(row.party) ??
          partyNames.full.get(row.party) ??
          null),
    key:
      row.party == null
        ? `${row.kind}:${(row.label ?? "").toLowerCase()}`
        : `party:${row.party}`,
    color: row.color,
  }));
}

async function states(
  found: { path: string; date: string; rest: string },
  plan: { shown: number; isFinal: boolean; from: number; every: number } | null,
) {
  const parts = found.path.split("/");
  if (found.rest !== "" || parts.length !== 2) {
    return [];
  }
  const result = await db.execute<{
    path: string;
    polity: string;
    contests: number;
    counting: number;
    certified: number;
    newest: string | null;
    reported: number | null;
  }>(sql`
    with kids as (
      select e.id, s.path, e.polity_id as polity, split_part(s.path, '/', 1) || '/' || split_part(s.path, '/', 2) || '/%' as under
      from slug s join election e on e.id = s.entity_id
      where s.is_current and e.date_from = ${found.date}::date and s.path ~ ${`^${parts[0]}/[a-z]{2}/${parts[1]}$`}
    )
    select k.path, k.polity::text, count(distinct c.id)::int as contests,
      count(distinct rs.contest_id) filter (where rs.stage in ('live', 'preliminary') and rs.as_of is not null)::int as counting,
      count(distinct rs.contest_id) filter (where rs.stage in ('official', 'certified') and rs.kind = 'counted')::int as certified,
      to_char(max(rs.as_of) filter (where rs.stage in ('live', 'preliminary')) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as newest,
      (select x.completeness::float8 from contest y join slug z on z.entity_id = y.id and z.is_current
         join result_set x on x.contest_id = y.id and x.status = 'active' and x.stage in ('live', 'preliminary') and x.as_of is not null
       where y.election_id = k.id
       order by array_position(array['governor', 'senate'], split_part(z.path, '/', 4)) nulls last, x.as_of desc limit 1) as reported
    from kids k
    join election e on e.date_from = ${found.date}::date
    join slug se on se.entity_id = e.id and se.is_current and se.path like k.under
    join contest c on c.election_id = e.id
    left join result_set rs on rs.contest_id = c.id and rs.status = 'active'
    group by k.id, k.path, k.polity
  `);
  return result.rows.map((row) => {
    const share =
      plan == null ? null : landing(row.path, Math.min(plan.shown, NIGHT));
    return plan == null
      ? row
      : {
          ...row,
          counting:
            share == null
              ? 0
              : Math.ceil(row.contests * Math.min(1, share * 2)),
          certified: share != null && plan.isFinal ? row.contests : 0,
          newest:
            share == null
              ? null
              : new Date(
                  plan.from + (Math.min(plan.shown, NIGHT) - 1) * plan.every,
                )
                  .toISOString()
                  .replace(/\.\d+Z$/u, "Z"),
          reported: share,
        };
  });
}

function scanners(note: string | null) {
  const list =
    /Share of scanners reported at this time: (.+)\.$/u.exec(note ?? "")?.[1] ??
    "";
  return list.split(", ").flatMap((part) => {
    const found = /^(.+) ([\d.]+)%$/u.exec(part);
    return found == null
      ? []
      : [{ area: found[1] ?? "", share: Number(found[2]) }];
  });
}

const Input = v.object({
  path: v.string(),
  rehearsal: v.optional(
    v.object({
      started: v.nullable(v.number()),
      shown: v.number(),
      isFinal: v.boolean(),
    }),
  ),
});

export const live = {
  get: strict
    .meta(
      openapi({
        method: "GET",
        path: "/live",
        summary:
          "An election night as the count came in: every update the official source published, each race's running totals by option at each update, the share of scanners reported where the source prints it, and the certified result once there is one",
      }),
    )
    .input(Input)
    .handler(async ({ input }) => {
      const found = await election(input.path);
      const plan =
        dev && input.rehearsal != null
          ? {
              ...input.rehearsal,
              from:
                input.rehearsal.started ??
                Date.parse(`${found.date}T00:20:00Z`) + 86_400_000,
              every: input.rehearsal.started == null ? 1_200_000 : 150_000,
            }
          : null;
      const staged = new Map<
        number,
        Array<{ option: string; votes: number; full: number }>
      >();
      const stamped =
        plan == null
          ? await sets(found.id)
          : await (async () => {
              const frame = await rehearsal(found.id, found.date);
              return Array.from(
                { length: Math.min(plan.shown, NIGHT) },
                (_, index) =>
                  [...Map.groupBy(frame(index), (row) => row.contest)].flatMap(
                    ([contest, rows]) => {
                      if (rows.every((row) => row.votes === 0)) {
                        return [];
                      }
                      const k = -1 - staged.size;
                      staged.set(k, rows);
                      let counted = 0;
                      let full = 0;
                      for (const row of rows) {
                        counted += row.votes;
                        full += row.full;
                      }
                      return [
                        {
                          k,
                          contest,
                          at: new Date(plan.from + index * plan.every)
                            .toISOString()
                            .replace(/\.\d+Z$/u, "Z"),
                          outside: false,
                          scheme: rows.some((row) => row.area != null)
                            ? "us.county"
                            : "us.state",
                          note: null,
                          publisher: "Rehearsal",
                          url: null,
                          reported: full > 0 ? counted / full : null,
                        },
                      ];
                    },
                  ),
              ).flat();
            })();
      const children = await states(found, plan);
      if (
        stamped.length === 0 &&
        children.every((row) => row.counting <= 0) &&
        Date.parse(found.date) < Date.now() - 2 * 86_400_000
      ) {
        throw new ORPCError("NOT_FOUND");
      }
      const bucket = new Map<string, string>();
      let end = "";
      const stamps = [...new Set(stamped.map((row) => row.at))].toSorted(
        (a, b) => b.localeCompare(a),
      );
      for (const at of stamps) {
        if (end === "" || Date.parse(end) - Date.parse(at) > 120_000) {
          end = at;
        }
        bucket.set(at, end);
      }
      const listed = stamped
        .map((row) => ({
          ...row,
          stamp: row.at,
          at: bucket.get(row.at) ?? row.at,
        }))
        .toSorted((a, b) => a.stamp.localeCompare(b.stamp));
      const settledOnly = await db.execute<{ id: string }>(sql`
        select c.id from contest c
        where ${stamped.length > 0 && plan?.isFinal !== false}::boolean and c.election_id = ${found.id}::uuid
          and exists (select from result_set rs where rs.contest_id = c.id and rs.stage in ('official', 'certified') and rs.status = 'active' and rs.kind = 'counted')
      `);
      const contests = [
        ...new Set([
          ...listed.map((row) => row.contest),
          ...settledOnly.rows.map((row) => row.id),
        ]),
      ];
      const [summed, settled, choices, titles, slugs] = await Promise.all([
        plan == null
          ? totals(listed.map((row) => row.k))
          : [...staged].flatMap(([set, rows]) =>
              [...Map.groupBy(rows, (row) => row.option)].map(
                ([option, lines]) => {
                  let votes = 0;
                  for (const line of lines) {
                    votes += line.votes;
                  }
                  return { set, option, votes };
                },
              ),
            ),
        plan?.isFinal === false ? [] : certified(contests),
        options(contests, found.date),
        names(
          [
            found.id,
            found.polity,
            ...contests,
            ...children.map((row) => row.polity),
          ],
          found.date,
        ),
        db.execute<{ id: string; path: string }>(sql`
          select entity_id as id, path from slug where is_current and entity_id = any(${sql.param(contests)}::uuid[])
        `),
      ]);
      const paths = new Map(slugs.rows.map((row) => [row.id, row.path]));
      const bySet = Map.groupBy(summed, (row) => row.set);
      const moments = [...new Set(listed.map((row) => row.at))].toSorted(
        (a, b) => a.localeCompare(b),
      );
      const frames = moments.map((at) => ({
        at,
        scanners: [
          ...new Map(
            listed
              .filter((row) => row.at === at)
              .flatMap((row) => scanners(row.note))
              .map((row) => [row.area, row.share]),
          ),
        ].map(([area, share]) => ({ area, share })),
      }));
      const races = contests.map((contest) => {
        const own = choices.filter((choice) => choice.contest === contest);
        const index = new Map(
          own.map((choice, position) => [choice.id, position]),
        );
        const mine = listed.filter((row) => row.contest === contest);
        const series = moments.flatMap((at) => {
          const chosen = mine
            .filter((row) => row.at === at && WHOLE.includes(row.scheme))
            .toSorted(
              (a, b) =>
                WHOLE.indexOf(a.scheme) - WHOLE.indexOf(b.scheme) ||
                b.stamp.localeCompare(a.stamp),
            )[0];
          if (chosen == null) {
            return [];
          }
          const votes = own.map(() => 0);
          const rows = bySet.get(chosen.k) ?? [];
          for (const row of rows) {
            const position = index.get(row.option);
            if (position != null) {
              votes[position] = row.votes;
            }
          }
          return [
            { at, level: chosen.scheme, votes, reported: chosen.reported },
          ];
        });
        const final = settled.filter((row) => row.contest === contest);
        const votes = own.map(() => 0);
        for (const row of final) {
          const position = index.get(row.option);
          if (position != null) {
            votes[position] = row.votes;
          }
        }
        const path = paths.get(contest) ?? "";
        const tally = final.length > 0 ? votes : (series.at(-1)?.votes ?? []);
        const top = Math.max(0, ...tally);
        return {
          id: contest,
          path,
          name: (titles.full.get(contest) ?? path.split("/").at(-1) ?? "")
            .replace(/^\d{4} /u, "")
            .replace(/ election$/u, "")
            .replace(/^\p{Ll}/u, (letter) => letter.toUpperCase()),
          group: group(path),
          options: own.map(({ id, name, party, key, color, kind }, at) => ({
            id,
            name,
            party,
            key,
            color:
              color ??
              (key.startsWith("party:") && (tally[at] ?? 0) >= top * 0.05
                ? (UNAFFILIATED[
                    Number.parseInt(key.slice(-4), 16) % UNAFFILIATED.length
                  ] ?? null)
                : null),
            kind,
          })),
          outside: mine.some((row) => row.outside),
          series,
          maps: mine
            .filter(
              (row) => row.scheme !== "us.place" && row.scheme !== "us.state",
            )
            .map((row) => ({ at: row.at, level: row.scheme }))
            .toSorted((a, b) => a.at.localeCompare(b.at)),
          certified:
            final.length === 0 || mine.some((row) => row.outside)
              ? null
              : {
                  votes,
                  elected: own.map((choice) =>
                    final.some(
                      (row) => row.option === choice.id && row.elected,
                    ),
                  ),
                },
        };
      });
      const source = listed[0];
      return {
        election: {
          id: found.id,
          path: found.path,
          name: titles.full.get(found.id) ?? found.path,
          place: titles.full.get(found.polity) ?? "",
          zone: ZONES[found.path.split("/", 2)[1] ?? ""] ?? "UTC",
          date: found.date,
        },
        focus: found.rest === "" ? null : `${found.path}/${found.rest}`,
        rehearsal: plan == null ? null : { from: plan.from, every: plan.every },
        source: {
          publisher: source?.publisher ?? "",
          url: source?.url ?? null,
        },
        frames,
        races: races.filter(
          (race) => race.series.length > 0 || race.certified != null,
        ),
        states: children
          .map((row) => {
            const code = row.path.split("/", 2)[1] ?? "";
            return {
              code,
              name: titles.full.get(row.polity) ?? code.toUpperCase(),
              path: row.path,
              zone: ZONES[code] ?? "UTC",
              status:
                row.certified > 0 &&
                row.certified >= row.counting &&
                Date.now() > Date.parse(found.date)
                  ? ("certified" as const)
                  : row.counting > 0
                    ? ("counting" as const)
                    : ("waiting" as const),
              contests: row.contests,
              counting: row.counting,
              newest: row.newest,
              reported: row.reported,
            };
          })
          .toSorted(
            (a, b) =>
              Number(TERRITORIES.has(a.code)) -
                Number(TERRITORIES.has(b.code)) || a.name.localeCompare(b.name),
          ),
      };
    }),
  stamp: base
    .input(v.object({ path: v.string(), isRehearsal: v.optional(v.boolean()) }))
    .handler(async ({ input }) => {
      if (dev && input.isRehearsal === true) {
        return { newest: new Date().toISOString(), isCertified: false };
      }
      const found = await election(input.path);
      const result = await db.execute<{
        newest: string | null;
        isCertified: boolean;
      }>(sql`
        select to_char(max(rs.as_of) filter (where rs.stage in ('live', 'preliminary')) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as newest,
          coalesce(bool_or(rs.stage in ('official', 'certified') and rs.kind = 'counted'), false) as "isCertified"
        from contest c join result_set rs on rs.contest_id = c.id and rs.status = 'active'
        where c.election_id = ${found.id}::uuid
      `);
      return {
        newest: result.rows[0]?.newest ?? "",
        isCertified: result.rows[0]?.isCertified ?? false,
      };
    }),
  map: os
    .input(
      v.object({
        contest: v.pipe(v.string(), v.uuid()),
        at: v.nullable(v.pipe(v.string(), v.isoTimestamp())),
        level: v.picklist(["us.nyc_ed", "us.nyc_ad", "us.county"]),
        rehearsal: v.optional(
          v.object({ from: v.number(), every: v.number() }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const plan = dev && input.at != null ? input.rehearsal : undefined;
      let chosen: { at: string | null; date: string };
      let values: Array<{
        area: number;
        id: string;
        option: string;
        votes: number;
      }>;
      if (plan == null) {
        const picked = await db.execute<{
          k: number;
          at: string | null;
          date: string;
        }>(sql`
          select rs.k, to_char(rs.as_of at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at, e.date_from::text as date
          from result_set rs join election e on e.id = rs.election_id
          where rs.contest_id = ${input.contest}::uuid and rs.status = 'active'
            and (${input.at}::timestamptz is null and rs.stage in ('official', 'certified')
              or rs.stage in ('live', 'preliminary') and rs.as_of <= ${input.at}::timestamptz)
            and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                        where v.result_set_k = rs.k and u.scheme_code = ${input.level} limit 1)
          order by (${input.at}::timestamptz is null and rs.is_default) desc, rs.as_of desc nulls last limit 1
        `);
        const row = picked.rows[0];
        if (row == null) {
          return null;
        }
        chosen = row;
        const found = await db.execute<{
          area: number;
          id: string;
          option: string;
          votes: number;
        }>(sql`
          select a.k as area, a.id, coalesce(o.parent_option_id, o.id) as option, sum(v.value)::float8 as votes
          from result_value v join reporting_unit u on u.k = v.unit_k and u.scheme_code = ${input.level}
          join area a on a.id = u.area_id
          join contest_option o on o.k = v.option_k
          where v.result_set_k = ${row.k} and v.measure = 1
          group by 1, 2, 3
        `);
        values = found.rows;
      } else {
        const held = await db.execute<{ election: string; date: string }>(sql`
          select c.election_id as election, e.date_from::text as date
          from contest c join election e on e.id = c.election_id where c.id = ${input.contest}::uuid
        `);
        const row = held.rows[0];
        if (row == null) {
          return null;
        }
        const frame = await rehearsal(row.election, row.date);
        chosen = { at: input.at, date: row.date };
        values = frame(
          Math.round((Date.parse(input.at ?? "") - plan.from) / plan.every),
        ).flatMap((entry) =>
          entry.contest === input.contest &&
          entry.area != null &&
          entry.id != null
            ? [
                {
                  area: entry.area,
                  id: entry.id,
                  option: entry.option,
                  votes: entry.votes,
                },
              ]
            : [],
        );
      }
      const scheme = input.level === "us.nyc_ad" ? "us.sldl" : input.level;
      const tiles = await db.execute<{
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
          from area_geometry g join area a on a.id = g.area_id
          where g.area_id = any(${sql.param([...new Set(values.map((row) => row.id))])}::uuid[])
            and g.valid @> ${chosen.date}::date
        ) x
        where bs.scheme_code = ${scheme} and bs.valid @> ${chosen.date}::date
        order by bs.valid_from desc nulls last limit 1
      `);
      const frame = tiles.rows[0];
      if (frame == null) {
        return null;
      }
      const titles = await names(
        [...new Set(values.map((row) => row.id))],
        chosen.date,
      );
      const areas = Map.groupBy(values, (row) => row.area);
      const options = [...new Set(values.map((row) => row.option))];
      const index = new Map(
        options.map((option, position) => [option, position]),
      );
      return {
        at: chosen.at,
        tiles: frame.tiles,
        frame: {
          west: frame.west,
          south: frame.south,
          east: frame.east,
          north: frame.north,
        },
        options,
        units: [...areas].map(([area, rows]) => {
          const votes = options.map(() => 0);
          for (const row of rows) {
            const position = index.get(row.option) ?? 0;
            votes[position] = (votes[position] ?? 0) + row.votes;
          }
          return {
            k: area,
            name: titles.full.get(rows[0]?.id ?? "") ?? "",
            votes,
          };
        }),
      };
    }),
};
