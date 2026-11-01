import type {
  LiveElection,
  LiveFrame,
  LiveOffice,
  LiveRace,
  LiveUnitCount,
} from "@ea/analytics/live";
import type { SQL } from "drizzle-orm";
import { dev } from "$app/env";
import { estimate } from "@ea/analytics/live";
import { openapi } from "@orpc/openapi";
import { os as base, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { os, strict } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { plane } from "#lib/server/plane.js";
import { names, UNAFFILIATED } from "#lib/server/rpc/archive.js";
import { landing, NIGHT, rehearsal } from "#lib/server/rpc/rehearsal.js";
import { Path } from "#lib/server/territory.js";

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

const CLOSES: Record<string, number> = {
  ga: 1140,
  in: 1140,
  ky: 1140,
  sc: 1140,
  vt: 1140,
  va: 1140,
  nc: 1170,
  oh: 1170,
  wv: 1170,
  al: 1200,
  ct: 1200,
  de: 1200,
  dc: 1200,
  fl: 1200,
  il: 1200,
  me: 1200,
  md: 1200,
  ma: 1200,
  ms: 1200,
  mo: 1200,
  nh: 1200,
  nj: 1200,
  ok: 1200,
  pa: 1200,
  ri: 1200,
  tn: 1200,
  ar: 1230,
  az: 1260,
  co: 1260,
  ia: 1260,
  ks: 1260,
  la: 1260,
  mi: 1260,
  mn: 1260,
  ne: 1260,
  nm: 1260,
  ny: 1260,
  sd: 1260,
  tx: 1260,
  wi: 1260,
  wy: 1260,
  mt: 1320,
  nv: 1320,
  nd: 1320,
  ut: 1320,
  ca: 1380,
  id: 1380,
  or: 1380,
  wa: 1380,
  hi: 1440,
  ak: 1500,
};
const TOP = /^(?:senate(?:-special)?|governor|house-(?:\d+|at-large))$/u;
const done = new Map<
  string,
  { key: string; value: Awaited<ReturnType<typeof night>> }
>();
const pending = new Map<
  string,
  { key: string; value: Promise<Awaited<ReturnType<typeof night>>> }
>();

function office(seat: string): LiveOffice {
  if (seat.startsWith("senate")) {
    return "senate";
  }
  if (seat.startsWith("house")) {
    return "house";
  }
  return seat === "governor" ? "governor" : "other";
}

function eastern(date: string, minutes: number) {
  const noon = new Date(`${date}T12:00:00Z`);
  const shown = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    hourCycle: "h23",
  }).format(noon);
  const offset = 12 - Number(shown);
  return new Date(noon.getTime() + (minutes - 720 + offset * 60) * 60_000)
    .toISOString()
    .replace(/\.\d+Z$/u, "Z");
}

async function races(found: {
  id: string;
  path: string;
  date: string;
  rest: string;
}) {
  const isNational = found.path.split("/").length === 2;
  const result =
    found.rest === ""
      ? await db.execute<{
          id: string;
          path: string;
          district: string | null;
        }>(sql`
          with recursive tree as (
            select id, 0 as depth from election where id = ${found.id}::uuid
            union all
            select e.id, t.depth + 1 from election e join tree t on e.parent_id = t.id where t.depth < ${isNational ? 1 : 3}
          ),
          top as (
            select c.id from tree t join contest c on c.election_id = t.id
            join slug s on s.entity_id = c.id and s.is_current
            where t.depth = ${isNational ? 1 : 0} and s.path ~ '^[a-z]+/[a-z]{2}/[^/]+/[^/]+$' and split_part(s.path, '/', 4) ~ ${TOP.source}
          ),
          fed as (
            select distinct f.contest_id as id from live.frame f join contest c on c.id = f.contest_id join tree t on t.id = c.election_id
            where f.status = 'active' and not ${isNational}::boolean
          )
          select c.id, s.path, c.district_area_id as district
          from contest c join slug s on s.entity_id = c.id and s.is_current
          where c.id in (select id from top union select id from fed)
        `)
      : await db.execute<{
          id: string;
          path: string;
          district: string | null;
        }>(sql`
          select c.id, s.path, c.district_area_id as district
          from slug s join contest c on c.id = s.entity_id
          where s.path = ${`${found.path}/${found.rest}`} and s.is_current
        `);
  return result.rows;
}

async function baselines(
  contests: Array<{ id: string; path: string; district: string | null }>,
  date: string,
) {
  const country = contests[0]?.path.split("/", 1)[0] ?? "";
  const generals = await db.execute<{ suffix: string }>(sql`
    select split_part(s.path, '/', 2) as suffix
    from election e join slug s on s.entity_id = e.id and s.is_current
    where e.polity_id = (select x.polity_id from slug y join election x on x.id = y.entity_id where y.path = ${`${country}/${date}-general`})
      and e.date_from < ${date}::date and e.date_from > ${date}::date - 4400
      and s.path ~ '^[a-z]+/[0-9]{4}-[0-9]{2}-[0-9]{2}-general$'
    order by e.date_from desc
  `);
  const wanted = contests.map((row) => {
    const parts = row.path.split("/");
    const seat = parts.at(-1) ?? "";
    return {
      ...row,
      paths: generals.rows.map(
        ({ suffix }) =>
          `${parts[0]}/${parts[1]}/${suffix}/${seat.startsWith("house") ? seat : "president"}`,
      ),
    };
  });
  const candidates = await db.execute<{
    path: string;
    id: string;
    district: string | null;
  }>(sql`
    select s.path, c.id, c.district_area_id as district
    from slug s join contest c on c.id = s.entity_id
    where s.path = any(${sql.param([...new Set(wanted.flatMap((row) => row.paths))])}::text[]) and s.is_current
      and exists (select from result_set rs where rs.contest_id = c.id and rs.stage in ('official', 'certified') and rs.status = 'active' and rs.kind = 'counted')
  `);
  const byPath = new Map(candidates.rows.map((row) => [row.path, row]));
  const pairs = wanted.flatMap((row) => {
    const prior = row.paths
      .map((path) => byPath.get(path))
      .find(
        (candidate) =>
          candidate != null &&
          (candidate.path.endsWith("/president") ||
            candidate.district === row.district),
      );
    return prior == null ? [] : [{ contest: row.id, prior: prior.id }];
  });
  if (pairs.length === 0) {
    return [];
  }
  const picked = await db.execute<{ prior: string; k: number }>(sql`
    select p.id as prior, x.k from unnest(${sql.param([...new Set(pairs.map((row) => row.prior))])}::uuid[]) as p(id) cross join lateral (
      select rs.k from result_set rs
      where rs.contest_id = p.id and rs.stage in ('official', 'certified') and rs.status = 'active' and rs.kind = 'counted'
        and (select u.scheme_code from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k limit 1) in ('us.county', 'us.county_part')
      order by rs.is_default desc, rs.stage = 'certified' desc limit 1
    ) x
  `);
  const priorOf = new Map(picked.rows.map((row) => [row.k, row.prior]));
  const found = await db.execute<{
    k: number;
    unit: string;
    party: string | null;
    votes: number;
  }>(sql`
    with counted as materialized (
      select v.result_set_k as k, v.unit_k, v.option_k, v.value from result_value v
      where v.result_set_k = any(${sql.param(picked.rows.map((row) => row.k))}::int[]) and v.measure = 1
    )
    select c.k, case when u.scheme_code = 'us.county' then u.id else u.parent_id end as unit,
      coalesce(top.party_id, o.party_id) as party, sum(c.value)::float8 as votes
    from counted c join reporting_unit u on u.k = c.unit_k
      and (u.scheme_code = 'us.county' or u.scheme_code = 'us.county_part' and u.parent_id is not null)
    join contest_option o on o.k = c.option_k
    join contest_option top on top.id = coalesce(o.parent_option_id, o.id)
    group by 1, 2, 3
  `);
  const byPrior = Map.groupBy(found.rows, (row) => priorOf.get(row.k) ?? "");
  return pairs.flatMap((pair) =>
    (byPrior.get(pair.prior) ?? []).map((row) => ({
      contest: pair.contest,
      unit: row.unit,
      party: row.party,
      votes: row.votes,
    })),
  );
}

async function night(
  found: { id: string; path: string; date: string; rest: string },
  picked: Array<{ id: string; path: string; district: string | null }>,
  detail: "race" | "state" | "national",
) {
  const contests = picked.map((row) => row.id);
  const [frames, choices, incumbents, settled, prior] = await Promise.all([
    db.execute<{
      k: number;
      contest: string;
      at: string;
      reporting: number | null;
      expected: number | null;
      publisher: string;
      url: string | null;
    }>(sql`
      select f.k, f.contest_id as contest, to_char(f.as_of at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at,
        f.reporting, f.expected, d.publisher, d.url
      from live.frame f join live.feed d on d.id = f.feed_id
      where f.contest_id = any(${sql.param(contests)}::uuid[]) and f.status = 'active'
      order by f.as_of
    `),
    options(contests, found.date),
    db.execute<{ option: string }>(sql`
      select ca.option_id as option from candidacy ca join contest_option o on o.id = ca.option_id
      where o.contest_id = any(${sql.param(contests)}::uuid[]) and ca.role = 'head' and ca.is_incumbent
    `),
    certified(contests),
    baselines(picked, found.date),
  ]);
  const values = await db.execute<{
    k: number;
    unit: string;
    option: string;
    votes: number;
  }>(sql`
    select v.frame_k as k, v.unit_id as unit, coalesce(o.parent_option_id, o.id) as option, sum(v.votes)::float8 as votes
    from live.value v join contest_option o on o.id = v.option_id
    where v.frame_k = any(${sql.param(frames.rows.map((row) => row.k))}::bigint[])
    group by 1, 2, 3
  `);
  const unitIds = [
    ...new Set([
      ...values.rows.map((row) => row.unit),
      ...prior.map((row) => row.unit),
    ]),
  ];
  const held = await db.execute<{
    id: string;
    area: string | null;
    scheme: string;
    parent: string | null;
  }>(sql`
    select id, area_id as area, scheme_code as scheme, parent_id as parent from reporting_unit where id = any(${sql.param(unitIds)}::uuid[])
  `);
  const parties = [
    ...new Set(
      choices.flatMap((choice) =>
        choice.key.startsWith("party:") ? [choice.key.slice(6)] : [],
      ),
    ),
  ];
  const [titles, partyNames] = await Promise.all([
    names(
      [
        found.id,
        ...contests,
        ...held.rows.flatMap((row) => (row.area == null ? [] : [row.area])),
      ],
      found.date,
    ),
    names(parties, found.date),
  ]);
  const sitting = new Set(incumbents.rows.map((row) => row.option));
  const unitOf = new Map(
    held.rows.map((row) => [
      row.id,
      {
        id: row.id,
        areaId: row.area,
        name: (row.area == null ? null : titles.full.get(row.area)) ?? "",
        scheme: row.scheme,
        parentId: row.parent,
      },
    ]),
  );
  const byFrame = Map.groupBy(values.rows, (row) => row.k);
  const byContest = Map.groupBy(frames.rows, (row) => row.contest);
  const priorBy = Map.groupBy(prior, (row) => row.contest);
  const settledBy = Map.groupBy(settled, (row) => row.contest);
  const built = picked.map((row): LiveRace => {
    const own = choices.filter((choice) => choice.contest === row.id);
    const index = new Map(own.map((choice, position) => [choice.id, position]));
    const mine = byContest.get(row.id) ?? [];
    const raceFrames: Array<LiveFrame> = [];
    const counts: Array<LiveUnitCount> = [];
    const last = new Map<string, string>();
    const seen = new Set<string>();
    for (const frame of mine) {
      const totals = own.map(() => 0);
      const byUnit = new Map<string, Array<number>>();
      for (const value of byFrame.get(frame.k) ?? []) {
        const position = index.get(value.option);
        if (position == null) {
          continue;
        }
        totals[position] = (totals[position] ?? 0) + value.votes;
        const votes = byUnit.get(value.unit) ?? own.map(() => 0);
        votes[position] = (votes[position] ?? 0) + value.votes;
        byUnit.set(value.unit, votes);
      }
      raceFrames.push({
        at: frame.at,
        totals,
        reporting: frame.reporting,
        expected: frame.expected,
      });
      for (const [unit, votes] of byUnit) {
        seen.add(unit);
        const key = votes.join(",");
        if (last.get(unit) !== key) {
          last.set(unit, key);
          counts.push({
            unitId: unit,
            at: frame.at,
            votes,
            reporting: null,
            expected: null,
          });
        }
      }
    }
    const partyIndex = new Map(
      own.flatMap((choice, position) =>
        choice.key.startsWith("party:")
          ? [[choice.key.slice(6), position] as const]
          : [],
      ),
    );
    const baseline = [
      ...Map.groupBy(priorBy.get(row.id) ?? [], (line) => line.unit),
    ].flatMap(([unit, lines]) => {
      let turnout = 0;
      const shares = own.map(() => 0);
      for (const line of lines) {
        turnout += line.votes;
        const position =
          line.party == null ? undefined : partyIndex.get(line.party);
        if (position != null) {
          shares[position] = (shares[position] ?? 0) + line.votes;
        }
      }
      if (turnout <= 0) {
        return [];
      }
      seen.add(unit);
      return [
        {
          unitId: unit,
          label: unitOf.get(unit)?.name ?? "",
          turnout,
          shares: shares.map((votes) => votes / turnout),
        },
      ];
    });
    const final = settledBy.get(row.id) ?? [];
    const certifiedTotals = own.map(() => 0);
    for (const line of final) {
      const position = index.get(line.option);
      if (position != null) {
        certifiedTotals[position] = line.votes;
      }
    }
    const tally = raceFrames.at(-1)?.totals ?? certifiedTotals;
    const top = Math.max(0, ...tally);
    const parts = row.path.split("/");
    const seat = parts.at(-1) ?? "";
    const source = mine.at(-1);
    return {
      id: row.id,
      slug: row.path,
      title: (titles.full.get(row.id) ?? seat)
        .replace(/^\d{4} /u, "")
        .replace(/ election$/u, "")
        .replace(/^\p{Ll}/u, (letter) => letter.toUpperCase()),
      office: office(seat),
      state: parts[1] ?? "",
      district: /^house-(.+)$/u.exec(seat)?.[1] ?? null,
      options: own.map((choice, at) => {
        const party = choice.key.startsWith("party:")
          ? choice.key.slice(6)
          : null;
        return {
          id: choice.id,
          label: choice.name,
          party:
            party == null
              ? null
              : {
                  id: party,
                  name: partyNames.full.get(party) ?? choice.party ?? "",
                  short:
                    partyNames.short.get(party) ??
                    partyNames.full.get(party) ??
                    "",
                  color:
                    choice.color ??
                    ((tally[at] ?? 0) >= top * 0.05
                      ? (UNAFFILIATED[
                          Number.parseInt(party.slice(-4), 16) %
                            UNAFFILIATED.length
                        ] ?? "#8a8a8a")
                      : "#8a8a8a"),
                },
          isWriteIn: choice.kind === "write_in",
          isIncumbent: sitting.has(choice.id),
        };
      }),
      units: [...seen].flatMap((unit) => {
        const known = unitOf.get(unit);
        return known == null ? [] : [known];
      }),
      frames: raceFrames,
      counts,
      baseline,
      certified:
        final.length === 0
          ? null
          : {
              totals: certifiedTotals,
              winners: own.flatMap((choice) =>
                final.some((line) => line.option === choice.id && line.elected)
                  ? [choice.id]
                  : [],
              ),
            },
      source: { publisher: source?.publisher ?? "", url: source?.url ?? null },
    };
  });
  const states = [...new Set(built.map((race) => race.state))].toSorted(
    (a, b) => a.localeCompare(b),
  );
  let newest: string | null = null;
  for (const frame of frames.rows) {
    if (newest == null || frame.at > newest) {
      newest = frame.at;
    }
  }
  const kept = (race: LiveRace) =>
    detail === "race" || (detail === "state" && race.office !== "other");
  const election: LiveElection = {
    id: found.id,
    slug: found.path,
    title: titles.full.get(found.id) ?? found.path,
    date: found.date,
    pollClose: states.flatMap((state) => {
      const minutes = CLOSES[state];
      return minutes == null
        ? []
        : [{ state, at: eastern(found.date, minutes) }];
    }),
    newest,
    isCertified:
      built.length > 0 && built.every((race) => race.certified != null),
    races: built.map((race) =>
      kept(race) ? race : { ...race, units: [], counts: [], baseline: [] },
    ),
  };
  return {
    election,
    estimates: Object.fromEntries(
      built.map((race) => {
        const value = estimate({ race, at: null });
        return [race.id, kept(race) ? value : { ...value, units: [] }];
      }),
    ),
  };
}

const Input = v.objectAsync({
  path: Path,
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
  election: base
    .meta(
      openapi({
        method: "GET",
        path: "/live/election",
        summary:
          "Unofficial election night counts as the official feeds publish them: each race's running totals and county counts at every update, the prior result the estimate compares against, and the estimate itself",
      }),
    )
    .input(v.objectAsync({ path: Path }))
    .handler(async ({ input }) => {
      const found = await election(input.path);
      const picked = await races(found);
      if (picked.length === 0) {
        throw new ORPCError("NOT_FOUND");
      }
      const stamp = await db.execute<{ key: string }>(sql`
        select coalesce(max(k), 0)::text || '|' || count(*)::text || '|' || coalesce(max(as_of)::text, '') as key
        from live.frame where contest_id = any(${sql.param(picked.map((row) => row.id))}::uuid[]) and status = 'active'
      `);
      const key = stamp.rows[0]?.key ?? "";
      const ready = done.get(input.path);
      if (ready?.key === key) {
        return ready.value;
      }
      let running = pending.get(input.path);
      if (running?.key !== key) {
        const value = night(
          found,
          picked,
          found.rest !== ""
            ? "race"
            : found.path.split("/").length === 2
              ? "national"
              : "state",
        );
        running = { key, value };
        pending.set(input.path, running);
        value
          .then((result) => {
            done.delete(input.path);
            done.set(input.path, { key, value: result });
            if (done.size > 64) {
              done.delete(done.keys().next().value ?? "");
            }
          })
          .catch((error: unknown) => {
            console.error(error);
          })
          .finally(() => {
            if (pending.get(input.path)?.value === value) {
              pending.delete(input.path);
            }
          });
      }
      return ready?.value ?? running.value;
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
        with recursive tree as (
          select id, 0 as depth from election where id = ${found.id}::uuid
          union all
          select e.id, t.depth + 1 from election e join tree t on e.parent_id = t.id where t.depth < 3
        ),
        frames as (
          select max(f.as_of) as newest from live.frame f join contest c on c.id = f.contest_id join tree t on t.id = c.election_id
          where f.status = 'active'
        )
        select to_char(greatest(max(rs.as_of) filter (where rs.stage in ('live', 'preliminary')), (select newest from frames)) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as newest,
          coalesce(bool_or(rs.stage in ('official', 'certified') and rs.kind = 'counted'), false) as "isCertified"
        from contest c left join result_set rs on rs.contest_id = c.id and rs.status = 'active'
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
