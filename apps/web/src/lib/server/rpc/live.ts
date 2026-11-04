import type {
  LiveBaseline,
  LiveElection,
  LiveFrame,
  LiveOffice,
  LiveOption,
  LiveRace,
  LiveUnit,
  LiveUnitCount,
} from "@ea/analytics/live";
import type { SQL } from "drizzle-orm";
import { dev } from "$app/env";
import { getRequestEvent } from "$app/server";
import { estimate } from "@ea/analytics/live";
import { openapi } from "@orpc/openapi";
import { os as base, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import { createHash } from "node:crypto";
import * as v from "valibot";
import { CLOSES, eastern } from "#lib/live/closes.js";
import { rule } from "#lib/measure.js";
import { baseLocale, getLocale } from "#lib/paraglide/runtime.js";
import { side, UNAFFILIATED } from "#lib/party.js";
import { os, remember } from "#lib/server/cache.js";
import { roles } from "#lib/server/contrib.js";
import counties from "#lib/server/county-traits.json";
import { liveDb as db } from "#lib/server/db/index.js";
import {
  framing,
  portraits,
  geography as shaped,
} from "#lib/server/live-board.js";
import { localizeNames } from "#lib/server/names.js";
import { names } from "#lib/server/rpc/archive.js";
import { landing, NIGHT, rehearsal } from "#lib/server/rpc/rehearsal.js";
import { Path } from "#lib/server/territory.js";

const TRAITS: Record<string, Array<number>> = counties;
const STAMP_MS = 5000;
const REFRESH_MS = 30_000;
const LAPSE_MS = 3 * 60 * 60 * 1000;
const stamps = new Map<
  string,
  { at: number; value: Promise<{ newest: string; isCertified: boolean }> }
>();

const LEVELS = new Set([
  "us.place",
  "us.state",
  "us.county",
  "us.nyc_ad",
  "us.nyc_ed",
]);
const TERRITORIES = new Set(["as", "gu", "mp", "pr", "vi"]);
const JURISDICTIONS: Record<
  string,
  NonNullable<LiveRace["jurisdiction"]>["kind"]
> = {
  county: "county",
  region: "state",
  municipality: "municipal",
  submunicipal: "municipal",
  school_district: "school",
  special_district: "special",
  judicial_district: "judicial",
  federal_unit: "state",
};
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

const tier = (feed: SQL, frame: SQL) =>
  sql`case
    when ${feed}.adapter = 'us.civicapi' and ${feed}.params->>'districts' = 'true' and ${frame}.reporting is not null then 1
    when ${feed}.adapter = 'us.npr' then 2
    when ${feed}.adapter = 'us.civicapi' then 3
    else 0
  end`;

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

async function newest(id: string, isShown: boolean) {
  const result = await db.execute<{
    newest: string | null;
    isCertified: boolean;
  }>(sql`
    with recursive tree as (
      select id, 0 as depth from election where id = ${id}::uuid
      union all
      select e.id, t.depth + 1 from election e join tree t on e.parent_id = t.id where t.depth < 3
    ),
    frames as (
      select max(f.as_of) as newest from live.frame f join contest c on c.id = f.contest_id join tree t on t.id = c.election_id
      where f.status = 'active' and ${isShown}::boolean
    ),
    called as (
      select max(l.called_at) as newest from live.call l join contest c on c.id = l.contest_id join contest_option o on o.id = l.option_id and o.contest_id = l.contest_id join tree t on t.id = c.election_id
      where ${isShown}::boolean
    )
    select to_char(greatest(max(rs.as_of) filter (where rs.stage in ('live', 'preliminary')), (select newest from frames), (select newest from called)) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as newest,
      coalesce(bool_or(rs.stage in ('official', 'certified') and rs.kind = 'counted'), false) as "isCertified"
    from contest c left join result_set rs on rs.contest_id = c.id and rs.status = 'active'
    where c.election_id = ${id}::uuid
  `);
  return {
    newest: result.rows[0]?.newest ?? "",
    isCertified: result.rows[0]?.isCertified ?? false,
  };
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
    family: string | null;
    color: string | null;
  }>(sql`
    select o.id, o.contest_id as contest, o.kind::text, o.ballot_label as label, ca.person_id as person, ca.ballot_name as ballot,
      line.party, coalesce(a.parent_party_id, line.party) as family, ${color(sql`line.party`, sql`${date}::date`)} as color
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
    left join party_affiliation a on a.party_id = line.party and a.kind = 'regional_branch' and a.valid @> ${date}::date
    where o.contest_id = any(${sql.param(contests)}::uuid[]) and o.parent_option_id is null
  `);
  const people = found.rows.flatMap((row) =>
    row.person == null ? [] : [row.person],
  );
  const parties = found.rows.flatMap((row) =>
    row.party == null ? [] : [row.party],
  );
  const [named, partyNames, faces] = await Promise.all([
    names(people, date),
    names(parties, date),
    portraits(people),
  ]);
  return found.rows.map((row) => ({
    id: row.id,
    contest: row.contest,
    kind: row.kind,
    person: row.person,
    photo: row.person == null ? null : (faces.get(row.person) ?? null),
    name: label(
      row.person == null ? null : named.full.get(row.person),
      row.ballot ?? row.label,
    ),
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
    family: row.kind === "write_in" ? null : row.family,
    color: row.color,
  }));
}

async function jurisdictions(contests: Array<string>) {
  const found = await db.execute<{
    contest: string;
    polity: string;
    kind: string;
    scheme: string | null;
    seats: number | null;
    rules: unknown;
    special: boolean;
    majority: boolean;
  }>(sql`
    select c.id as contest, c.term = 'remainder' as special, coalesce(es.rules ->> 'formula' in ('two_round', 'irv'), false) as majority,
      case when p.kind = 'sovereign_state' then coalesce(st.entity_id, p.id) else p.id end as polity,
      case
        when p.kind = 'sovereign_state' and st.entity_id is not null then 'federal_unit'
        when p.kind = 'county' and split_part(cs.path, '/', 5) ~ '^(county-)?(board-of-education|school-board|board-of-school|school-committee)' then 'school_district'
        else p.kind::text
      end as kind,
      a.scheme_code as scheme,
      case when b.kind = 'electoral_college' then 1 else c.magnitude end as seats,
      case when c.kind = 'ballot_question' then es.rules || coalesce(c.rules_override, '{}') end as rules
    from contest c join election e on e.id = c.election_id
    left join electoral_system es on es.id = c.electoral_system_id
    left join body b on b.id = c.body_id
    left join office o on o.id = c.office_id
    join polity p on p.id = coalesce(b.polity_id, o.polity_id, e.polity_id)
    left join area a on a.id = c.district_area_id
    left join slug cs on cs.entity_id = c.id and cs.is_current
    left join slug st on st.is_current and st.path = 'us/' || split_part(cs.path, '/', 2) and p.kind = 'sovereign_state'
    where c.id = any(${sql.param(contests)}::uuid[])
  `);
  return new Map(found.rows.map((row) => [row.contest, row]));
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

const TOP = /^(?:senate(?:-special)?|governor|house-(?:\d+|at-large))$/u;
const STATEWIDE = [
  "senate",
  "senate-special",
  "governor",
  "lieutenant-governor",
  "attorney-general",
  "secretary-of-state",
];

type Shown = { results: boolean; forecasts: boolean };

type Kept<T> = {
  ready?: { key: string; value: T };
  running?: Promise<T>;
  started: number;
  took: number;
};

async function settle<T>(
  kept: Map<string, Kept<T>>,
  slot: string,
  key: string,
  compute: () => Promise<T>,
  limit: number,
): Promise<T> {
  const entry = kept.get(slot) ?? { started: 0, took: 0 };
  kept.delete(slot);
  kept.set(slot, entry);
  if (kept.size > limit) {
    kept.delete(kept.keys().next().value ?? "");
  }
  const { ready } = entry;
  if (ready?.key === key) {
    return ready.value;
  }
  if (
    entry.running == null &&
    (ready == null ||
      Date.now() - entry.started >= Math.max(REFRESH_MS, 4 * entry.took))
  ) {
    const running = compute();
    entry.started = Date.now();
    entry.running = running;
    running
      .then((value) => {
        entry.ready = { key, value };
        entry.took = Date.now() - entry.started;
      })
      .catch((error: unknown) => {
        console.error(error);
      })
      .finally(() => {
        if (entry.running === running) {
          entry.running = undefined;
        }
      });
  }
  if (ready != null) {
    return ready.value;
  }
  return entry.running ?? compute();
}

const nights = new Map<string, Kept<Awaited<ReturnType<typeof night>>>>();

type Replayed = {
  totals: Array<[string, number]>;
  units: Array<[string, Array<[string, number]>]>;
};

type Replay = {
  ks: Array<number>;
  frames: Map<number, Replayed>;
  last: Map<string, string>;
};

const replays = new Map<string, Replay>();
const interned = new Map<string, string>();

function intern(text: string) {
  const known = interned.get(text);
  if (known != null) {
    return known;
  }
  interned.set(text, text);
  return text;
}

function leads(head: Array<number>, whole: Array<number>) {
  return head.length <= whole.length && head.every((k, at) => whole[at] === k);
}

async function framed(ks: Array<number>) {
  if (ks.length === 0) {
    return new Map<
      number,
      Array<{ unit: string; option: string; votes: number }>
    >();
  }
  const result = await db.execute<{
    k: number;
    unit: string;
    option: string;
    votes: number;
  }>(sql`
    select v.frame_k as k, v.unit_id as unit, coalesce(o.parent_option_id, o.id) as option, sum(v.votes)::float8 as votes
    from live.value v join contest_option o on o.id = v.option_id
    where v.frame_k = any(${sql.param(ks)}::bigint[])
    group by 1, 2, 3
  `);
  return Map.groupBy(result.rows, (row) => row.k);
}

function extend(
  kept: Replay,
  ks: Array<number>,
  byK: Awaited<ReturnType<typeof framed>>,
) {
  for (const k of ks.slice(kept.ks.length)) {
    const totals = new Map<string, number>();
    const pairs = new Map<string, Array<[string, number]>>();
    for (const row of byK.get(k) ?? []) {
      const option = intern(row.option);
      const unit = intern(row.unit);
      totals.set(option, (totals.get(option) ?? 0) + row.votes);
      const held = pairs.get(unit) ?? [];
      held.push([option, row.votes]);
      pairs.set(unit, held);
    }
    const units: Replayed["units"] = [];
    for (const [unit, votes] of pairs) {
      const key = votes
        .map(([option, value]) => `${option}:${value}`)
        .toSorted((x, y) => x.localeCompare(y, "en"))
        .join(";");
      if (kept.last.get(unit) !== key) {
        kept.last.set(unit, key);
        units.push([unit, votes]);
      }
    }
    kept.ks.push(k);
    kept.frames.set(k, { totals: [...totals], units });
  }
}

async function replay(frames: Array<{ k: number; contest: string }>) {
  const bases = [...Map.groupBy(frames, (row) => row.contest)].map(
    ([contest, rows]) => {
      const ks = rows.map((row) => row.k);
      let kept = replays.get(contest);
      if (kept == null || !leads(kept.ks, ks)) {
        kept = { ks: [], frames: new Map(), last: new Map() };
        replays.set(contest, kept);
      }
      return { contest, kept, ks, from: kept.ks.length };
    },
  );
  const byK = await framed(bases.flatMap(({ ks, from }) => ks.slice(from)));
  const sorted = Map.groupBy(bases, ({ kept, ks }) =>
    leads(kept.ks, ks) || leads(ks, kept.ks) ? "settled" : "crossed",
  );
  const redone = (sorted.get("crossed") ?? []).map(({ contest, ks }) => {
    const kept: Replay = { ks: [], frames: new Map(), last: new Map() };
    replays.set(contest, kept);
    return { kept, ks };
  });
  const again = await framed(redone.flatMap(({ ks }) => ks));
  const entries = new Map<number, Replayed>();
  for (const [{ kept, ks }, source] of [
    ...(sorted.get("settled") ?? []).map((base) => [base, byK] as const),
    ...redone.map((base) => [base, again] as const),
  ]) {
    extend(kept, ks, source);
    for (const k of ks) {
      const entry = kept.frames.get(k);
      if (entry != null) {
        entries.set(k, entry);
      }
    }
  }
  return entries;
}

async function stored() {
  const result = await db.execute<Shown>(sql`
    select coalesce(bool_or(shown) filter (where name = 'results'), false) as results,
      coalesce(bool_or(shown) filter (where name = 'forecasts'), false) as forecasts
    from live.switch
  `);
  return result.rows[0] ?? { results: false, forecasts: false };
}

async function switches() {
  const [found, role] = await Promise.all([
    stored(),
    roles(getRequestEvent().locals.user?.id),
  ]);
  return role.admin ? { ...found, results: true, forecasts: true } : found;
}

function office(seat: string): LiveOffice {
  if (/^senate(?:-special)?$/u.test(seat)) {
    return "senate";
  }
  if (/^house-(?:\d+|at-large)$/u.test(seat)) {
    return "house";
  }
  return seat === "governor" ? "governor" : "other";
}

async function races(found: {
  id: string;
  path: string;
  date: string;
  rest: string;
}) {
  if (found.rest === "" && found.path.split("/").length === 2) {
    return remember(`live:races:v1:${found.path}`, async () =>
      listRaces(found),
    );
  }
  return listRaces(found);
}

async function listRaces(found: {
  id: string;
  path: string;
  date: string;
  rest: string;
}) {
  const isNational = found.path.split("/").length === 2;
  const isLocal = found.path.split("/").length > 3;
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
            where t.depth = ${isNational ? 1 : 0}
              and (${isLocal}::boolean or (s.path ~ '^[a-z]+/[a-z]{2}/[^/]+/[^/]+$' and split_part(s.path, '/', 4) ~ ${TOP.source}))
          ),
          fed as (
            select distinct f.contest_id as id from live.frame f join contest c on c.id = f.contest_id join tree t on t.id = c.election_id
            where f.status = 'active' and not ${isNational}::boolean
          )
          select c.id, s.path, c.district_area_id as district
          from contest c join slug s on s.entity_id = c.id and s.is_current
          where c.id in (select id from top union select id from fed)
            and exists (select from contest_option o where o.contest_id = c.id)
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

function label(full: string | null | undefined, ballot: string | null) {
  if (full != null && !full.includes(" ") && ballot?.endsWith(` ${full}`)) {
    return ballot;
  }
  return full ?? ballot ?? "";
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
    const at = (suffix: string, name: string) =>
      `${parts[0]}/${parts[1]}/${suffix}/${name}`;
    return {
      ...row,
      seats:
        seat.startsWith("house") ||
        (parts.length === 4 && (TOP.test(seat) || STATEWIDE.includes(seat)))
          ? generals.rows.flatMap(({ suffix }) =>
              (seat === "house-0" ? [seat, "house-at-large"] : [seat]).map(
                (name) => at(suffix, name),
              ),
            )
          : [],
      presidents:
        parts.length === 4 && (TOP.test(seat) || STATEWIDE.includes(seat))
          ? generals.rows.map(({ suffix }) => at(suffix, "president"))
          : [],
    };
  });
  const scaling = ratios(
    wanted,
    date,
    generals.rows.map((row) => row.suffix),
  );
  const candidates = await db.execute<{
    path: string;
    id: string;
    district: string | null;
  }>(sql`
    select s.path, c.id, c.district_area_id as district
    from slug s join contest c on c.id = s.entity_id
    where s.path = any(${sql.param([...new Set(wanted.flatMap((row) => [...row.seats, ...row.presidents]))])}::text[]) and s.is_current
      and exists (select from result_set rs where rs.contest_id = c.id and rs.stage in ('official', 'certified') and rs.status = 'active' and rs.kind = 'counted')
  `);
  const byPath = new Map(candidates.rows.map((row) => [row.path, row]));
  const pairs = wanted.flatMap((row) => {
    const seat = row.seats
      .map((path) => byPath.get(path))
      .find((candidate) => candidate?.district === row.district);
    const president = row.presidents
      .map((path) => byPath.get(path))
      .find((candidate) => candidate != null);
    return [
      ...(seat == null || (president != null && !row.path.includes("/house"))
        ? []
        : [{ contest: row.id, prior: seat.id, kind: "seat" as const }]),
      ...(president == null
        ? []
        : [
            {
              contest: row.id,
              prior: president.id,
              kind: "president" as const,
            },
          ]),
    ];
  });
  if (pairs.length === 0) {
    return { lines: [], ratios: await scaling };
  }
  const picked = await db.execute<{ prior: string; k: number }>(sql`
    select p.id as prior, x.k from unnest(${sql.param([...new Set(pairs.map((row) => row.prior))])}::uuid[]) as p(id) cross join lateral (
      select rs.k from result_set rs
      where rs.contest_id = p.id and rs.stage in ('official', 'certified') and rs.status = 'active' and rs.kind = 'counted'
        and (select u.scheme_code in ('us.county', 'us.county_part') or u.scheme_code = 'us.state' and u.code in ('60', '66', '69', '72', '78')
            or u.scheme_code = 'us.cd' and left(u.code, 2) in ('60', '66', '69', '78')
          from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k limit 1)
      order by (select sum(v.value) from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k and v.measure = 1 and u.scheme_code in ('us.county', 'us.county_part')) desc nulls last,
        rs.is_default desc, rs.stage = 'certified' desc limit 1
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
    ), lines as (
      select c.option_k, sum(c.value) as votes from counted c group by 1
    ), tops as (
      select distinct coalesce(o.parent_option_id, o.id) as id
      from lines l join contest_option o on o.k = l.option_k
    ), reach as (
      select x.party_id, count(*) as n from contest_option x
      where x.party_id in (
        select line.party_id from tops t join contest_option line on line.parent_option_id = t.id
      )
      group by 1
    ), lead as (
      select distinct on (top.id) top.id as top, coalesce(top.party_id, line.party_id) as party
      from tops t join contest_option top on top.id = t.id
      left join contest_option line on top.party_id is null and line.parent_option_id = top.id and line.party_id is not null
      left join lines l on l.option_k = line.k
      left join reach r on r.party_id = line.party_id
      order by top.id, l.votes desc nulls last, r.n desc nulls last
    )
    select c.k, case when u.scheme_code in ('us.county_part', 'us.cd') then u.parent_id else u.id end as unit,
      coalesce(a.parent_party_id, lead.party) as party, sum(c.value)::float8 as votes
    from counted c join reporting_unit u on u.k = c.unit_k
      and (u.scheme_code = 'us.county' or u.scheme_code = 'us.county_part' and u.parent_id is not null
        or (u.scheme_code = 'us.state' and u.code in ('60', '66', '69', '72', '78')
          or u.scheme_code = 'us.cd' and left(u.code, 2) in ('60', '66', '69', '78') and u.parent_id is not null)
          and not exists (select from counted w join reporting_unit y on y.k = w.unit_k where w.k = c.k and y.scheme_code in ('us.county', 'us.county_part')))
    join contest_option o on o.k = c.option_k
    join lead on lead.top = coalesce(o.parent_option_id, o.id)
    left join party_affiliation a on a.party_id = lead.party and a.kind = 'regional_branch' and a.valid @> ${date}::date
    group by 1, 2, 3
  `);
  const byPrior = Map.groupBy(found.rows, (row) => priorOf.get(row.k) ?? "");
  return {
    lines: pairs.flatMap((pair) =>
      (byPrior.get(pair.prior) ?? []).map((row) => ({
        contest: pair.contest,
        kind: pair.kind,
        unit: row.unit,
        party: row.party,
        votes: row.votes,
      })),
    ),
    ratios: await scaling,
  };
}

const scaled = new Map<string, Promise<Map<string, number>>>();

async function load(state: string, midterm: string, presidential: string) {
  const paths = [
    `${state}/${presidential}/president`,
    ...STATEWIDE.map((seat) => `${state}/${midterm}/${seat}`),
  ];
  const result = await db.execute<{
    unit: string;
    midterm: number | null;
    presidential: number | null;
  }>(sql`
    with chosen as (
      select c.id, split_part(s.path, '/', 3) = ${midterm} as midterm
      from slug s join contest c on c.id = s.entity_id
      where s.path = any(${sql.param(paths)}::text[]) and s.is_current
    ), sets as (
      select ch.midterm, x.k from chosen ch cross join lateral (
        select rs.k from result_set rs
        where rs.contest_id = ch.id and rs.stage in ('official', 'certified') and rs.status = 'active' and rs.kind = 'counted'
          and (select u.scheme_code from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k limit 1) in ('us.county', 'us.county_part')
        order by (select sum(v.value) from result_value v join reporting_unit u on u.k = v.unit_k where v.result_set_k = rs.k and v.measure = 1 and u.scheme_code in ('us.county', 'us.county_part')) desc nulls last,
          rs.is_default desc, rs.stage = 'certified' desc limit 1
      ) x
    ), totals as (
      select s.k, s.midterm, u.unit, sum(v.value)::float8 as votes
      from sets s join result_value v on v.result_set_k = s.k and v.measure = 1
      cross join lateral (
        select case when u.scheme_code = 'us.county' then u.id else u.parent_id end as unit from reporting_unit u
        where u.k = v.unit_k and (u.scheme_code = 'us.county' or u.scheme_code = 'us.county_part' and u.parent_id is not null)
        offset 0
      ) u
      group by 1, 2, 3
    )
    select unit, max(votes) filter (where midterm) as midterm, max(votes) filter (where not midterm) as presidential
    from totals group by unit
  `);
  const ratio = new Map<string, number>();
  for (const row of result.rows) {
    if (
      row.midterm != null &&
      row.presidential != null &&
      row.presidential > 0
    ) {
      ratio.set(row.unit, row.midterm / row.presidential);
    }
  }
  return ratio;
}

async function scale(state: string, midterm: string, presidential: string) {
  const key = `${state}/${midterm}/${presidential}`;
  const held = scaled.get(key);
  if (held != null) {
    return held;
  }
  const running = load(state, midterm, presidential);
  scaled.set(key, running);
  try {
    return await running;
  } catch (error) {
    scaled.delete(key);
    throw error;
  }
}

async function ratios(
  contests: Array<{ path: string }>,
  date: string,
  suffixes: Array<string>,
) {
  const year = Number(date.slice(0, 4));
  const back = year - 4;
  const midterm = suffixes.find((suffix) => suffix.startsWith(`${back}-`));
  const presidential = suffixes.find((suffix) =>
    suffix.startsWith(`${back - (back % 4)}-`),
  );
  const ratio = new Map<string, number>();
  if (year % 4 === 0 || midterm == null || presidential == null) {
    return ratio;
  }
  const states = new Set(
    contests.map((row) => row.path.split("/").slice(0, 2).join("/")),
  );
  for (const part of await Promise.all(
    [...states].map(async (state) => scale(state, midterm, presidential)),
  )) {
    for (const [unit, value] of part) {
      ratio.set(unit, value);
    }
  }
  return ratio;
}

async function geography(
  contests: Array<{ id: string; path: string; district: string | null }>,
  date: string,
  isWide: boolean,
) {
  const house = contests.filter((row) => /\/house-[^/]+$/u.test(row.path));
  const statewide = contests.filter(
    (row) => !house.includes(row) && TOP.test(row.path.split("/").at(-1) ?? ""),
  );
  const local = isWide
    ? contests.filter(
        (row) =>
          !house.includes(row) &&
          !statewide.includes(row) &&
          row.district != null,
      )
    : [];
  const wide = [...statewide, ...local].map((row) => row.id);
  const result = await db.execute<{ contest: string; unit: string }>(sql`
    with h as (
      select c.id as contest, g.geom from contest c
      join area_geometry g on g.area_id = c.district_area_id and g.valid @> ${date}::date
      where c.id = any(${sql.param(house.map((row) => row.id))}::uuid[])
    ), l as materialized (
      select c.id as contest, g.geom from contest c
      join area a on a.id = c.district_area_id and a.scheme_code not in ('us.state', 'us.county')
      join area_geometry g on g.area_id = a.id and g.valid @> ${date}::date
      where c.id = any(${sql.param(local.map((row) => row.id))}::uuid[])
    ), near as materialized (
      select l.contest, l.geom as d, u.id, cg.geom from l
      join area_geometry cg on cg.geom && l.geom and cg.valid @> ${date}::date
      join reporting_unit u on u.area_id = cg.area_id and u.scheme_code = 'us.county' and u.valid @> ${date}::date
    )
    select c.id as contest, u.id as unit from contest c
    join reporting_unit s on s.area_id = c.district_area_id and s.scheme_code = 'us.state' and s.valid @> ${date}::date
    join reporting_unit u on u.parent_id = s.id and u.scheme_code = 'us.county' and u.valid @> ${date}::date
    where c.id = any(${sql.param(wide)}::uuid[])
    union
    select c.id, u.id from contest c
    join reporting_unit u on u.area_id = c.district_area_id and u.scheme_code = 'us.county' and u.valid @> ${date}::date
    where c.id = any(${sql.param(local.map((row) => row.id))}::uuid[])
    union
    select h.contest, u.id from h
    join area_geometry cg on cg.geom && h.geom and cg.valid @> ${date}::date
    join reporting_unit u on u.area_id = cg.area_id and u.scheme_code = 'us.county' and u.valid @> ${date}::date
    where st_area(st_intersection(cg.geom, h.geom)) > 0.005 * st_area(cg.geom)
    union
    select contest, id from near
    where st_area(st_intersection(geom, d)) > least(0.005 * st_area(geom), 0.05 * st_area(d))
  `);
  return result.rows;
}

async function calls(contests: Array<string>) {
  const result = await db.execute<{
    contest: string;
    option: string;
    at: string;
  }>(sql`
    select l.contest_id as contest, l.option_id as option, to_char(l.called_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at
    from live.call l
    join contest c on c.id = l.contest_id
    join contest_option o on o.id = l.option_id and o.contest_id = l.contest_id
    where l.contest_id = any(${sql.param(contests)}::uuid[])
  `);
  return result.rows;
}

async function localized(titles: {
  full: Map<string, string>;
  short: Map<string, string>;
}) {
  const locale = getLocale();
  if (locale === baseLocale) {
    return titles;
  }
  const { full, short } = await localizeNames(
    {
      full: Object.fromEntries(titles.full),
      short: Object.fromEntries(titles.short),
    },
    locale,
  );
  return {
    full: new Map(Object.entries(full)),
    short: new Map(Object.entries(short)),
  };
}

async function night(
  found: { id: string; path: string; date: string; rest: string },
  picked: Array<{ id: string; path: string; district: string | null }>,
  detail: "race" | "state" | "national",
  shown: Shown,
  called: Awaited<ReturnType<typeof calls>>,
  slot = "",
) {
  const contests = picked.map((row) => row.id);
  const roster = createHash("sha1")
    .update(contests.toSorted((x, y) => x.localeCompare(y, "en")).join(","))
    .digest("base64url");
  const [
    frames,
    { choices, incumbents, settled, prior, drawn, placed },
    feeds,
    polities,
  ] = await Promise.all([
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
      where f.contest_id = any(${sql.param(contests)}::uuid[]) and f.status = 'active' and ${shown.results}::boolean
        and not exists (
          select from live.frame o join live.feed od on od.id = o.feed_id
          where o.contest_id = f.contest_id and o.status = 'active' and od.enabled
            and ${tier(sql.raw("od"), sql.raw("o"))} < ${tier(sql.raw("d"), sql.raw("f"))}
            and exists (
              select from live.fetch x where x.feed_id = od.id and x.status <> 'error'
                and x.fetched_at > now() - interval '20 minutes'
            )
        )
      order by f.as_of, f.k
    `),
    remember(
      `live:fixed:v3:${found.path}|${found.rest}|${detail}${slot}|${roster}`,
      async () => {
        const [choices, incumbents, settled, prior, drawn, placed] =
          await Promise.all([
            options(contests, found.date),
            db.execute<{ option: string }>(sql`
              select ca.option_id as option from candidacy ca join contest_option o on o.id = ca.option_id
              where o.contest_id = any(${sql.param(contests)}::uuid[]) and ca.role = 'head' and ca.is_incumbent
            `),
            certified(contests),
            baselines(picked, found.date),
            detail === "national"
              ? []
              : geography(
                  picked,
                  found.date,
                  detail === "race" || found.path.split("/").length > 3,
                ),
            jurisdictions(contests),
          ]);
        return {
          choices,
          incumbents: incumbents.rows,
          settled,
          prior,
          drawn,
          placed,
        };
      },
    ),
    db.execute<{ state: string; isRunning: boolean }>(sql`
      select lower(d.params->>'state') as state,
        bool_or(${shown.results}::boolean and exists (
          select from live.frame f join contest c on c.id = f.contest_id join election e on e.id = c.election_id
          where f.feed_id = d.id and f.status = 'active' and e.date_from = ${found.date}::date
        )) as "isRunning"
      from live.feed d where d.enabled and d.params ? 'state'
      group by 1
    `),
    db.execute<{ state: string; id: string }>(sql`
      select lower(substr(iso_code, 4)) as state, id from polity where iso_code ~ '^US-[A-Z]{2}$'
    `),
  ]);
  const fed = new Map(feeds.rows.map((row) => [row.state, row.isRunning]));
  const lapsed = new Map<string, "none" | "outside">();
  for (const [state, minutes] of Object.entries(CLOSES)) {
    if (!fed.has(state)) {
      lapsed.set(state, "none");
    } else if (
      fed.get(state) === true &&
      Date.now() > Date.parse(eastern(found.date, minutes)) + LAPSE_MS
    ) {
      lapsed.set(state, "outside");
    }
  }
  const polityOf = new Map(
    polities.rows.flatMap((row) =>
      lapsed.has(row.state) ? [[row.state, row.id] as const] : [],
    ),
  );
  const replayed = await replay(frames.rows);
  const unitIds = [
    ...new Set([
      ...replayed
        .values()
        .flatMap((entry) => entry.units.map(([unit]) => unit)),
      ...prior.lines.map((row) => row.unit),
      ...drawn.map((row) => row.unit),
    ]),
  ];
  const held = await db.execute<{
    id: string;
    area: string | null;
    code: string;
    scheme: string;
    parent: string | null;
  }>(sql`
    select id, code, area_id as area, scheme_code as scheme, parent_id as parent from reporting_unit where id = any(${sql.param(unitIds)}::uuid[])
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
        ...polityOf.values(),
        ...placed.values().map((row) => row.polity),
        ...held.rows.flatMap((row) => (row.area == null ? [] : [row.area])),
      ],
      found.date,
    ).then(localized),
    names(parties, found.date),
  ]);
  const sitting = new Set(incumbents.map((row) => row.option));
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
  const fold = (unit: string) => {
    const known = unitOf.get(unit);
    const parent = known?.parentId == null ? null : unitOf.get(known.parentId);
    return parent?.areaId != null && parent.areaId === known?.areaId
      ? parent.id
      : unit;
  };
  const traitsOf = new Map(
    held.rows.flatMap((row) => {
      const traits = row.scheme === "us.county" ? TRAITS[row.code] : undefined;
      return traits == null ? [] : [[row.id, traits] as const];
    }),
  );
  const byContest = Map.groupBy(frames.rows, (row) => row.contest);
  const priorBy = Map.groupBy(prior.lines, (row) => row.contest);
  const settledBy = Map.groupBy(settled, (row) => row.contest);
  const drawnBy = Map.groupBy(drawn, (row) => row.contest);
  const badges = new Map<string, NonNullable<LiveOption["party"]>>();
  const calledBy = new Map(called.map((row) => [row.contest, row]));
  const built = picked.map((row): LiveRace => {
    const own = choices.filter((choice) => choice.contest === row.id);
    const index = new Map(own.map((choice, position) => [choice.id, position]));
    const mine = byContest.get(row.id) ?? [];
    const raceFrames: Array<LiveFrame> = [];
    const counts: Array<LiveUnitCount> = [];
    const last = new Map<string, string>();
    const seen = new Set<string>(
      (drawnBy.get(row.id) ?? []).map((line) => fold(line.unit)),
    );
    const current = new Map<string, Array<number>>();
    const members = new Map<string, Set<string>>();
    for (const frame of mine) {
      const entry = replayed.get(frame.k);
      const totals = own.map(() => 0);
      for (const [option, votes] of entry?.totals ?? []) {
        const position = index.get(option);
        if (position != null) {
          totals[position] = (totals[position] ?? 0) + votes;
        }
      }
      const byUnit = new Map<string, Array<number>>();
      for (const [unit, pairs] of entry?.units ?? []) {
        for (const [option, value] of pairs) {
          const position = index.get(option);
          if (position == null) {
            continue;
          }
          const votes = byUnit.get(unit) ?? own.map(() => 0);
          votes[position] = (votes[position] ?? 0) + value;
          byUnit.set(unit, votes);
        }
      }
      raceFrames.push({
        at: frame.at,
        totals,
        reporting: frame.reporting,
        expected: frame.expected,
      });
      const touched = new Set<string>();
      for (const [unit, votes] of byUnit) {
        current.set(unit, votes);
        const folded = fold(unit);
        members.set(folded, (members.get(folded) ?? new Set()).add(unit));
        touched.add(folded);
      }
      for (const unit of touched) {
        seen.add(unit);
        const votes = own.map(() => 0);
        for (const member of members.get(unit) ?? []) {
          for (const [position, value] of (
            current.get(member) ?? []
          ).entries()) {
            votes[position] = (votes[position] ?? 0) + value;
          }
        }
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
    const lines = (priorBy.get(row.id) ?? []).map((line) => ({
      ...line,
      unit: fold(line.unit),
    }));
    const seats = lines.filter((line) => line.kind === "seat");
    const reach = new Set(
      seats.length > 0
        ? seats.map((line) => line.unit)
        : (drawnBy.get(row.id) ?? []).map((line) => line.unit),
    );
    const house = /\/house-[^/]+$/u.test(row.path);
    const presidents = lines.filter(
      (line) => line.kind === "president" && (!house || reach.has(line.unit)),
    );
    const latest = raceFrames.at(-1)?.totals ?? [];
    const allot = (source: typeof lines) => {
      let all = 0;
      const byParty = new Map<string, number>();
      for (const line of source) {
        all += line.votes;
        if (line.party != null) {
          byParty.set(line.party, (byParty.get(line.party) ?? 0) + line.votes);
        }
      }
      const credit = new Map<string, Array<number>>();
      for (const [position, choice] of own.entries()) {
        if (choice.family != null) {
          credit.set(choice.family, [
            ...(credit.get(choice.family) ?? []),
            position,
          ]);
        }
      }
      const free = own.flatMap((choice, position) =>
        choice.kind !== "write_in" &&
        (choice.family == null ||
          (byParty.get(choice.family) ?? 0) < 0.05 * all)
          ? [position]
          : [],
      );
      const orphan = [...byParty]
        .filter(([party]) => !credit.has(party))
        .toSorted((x, y) => y[1] - x[1])[0];
      if (orphan != null && orphan[1] >= 0.15 * all) {
        const stand =
          free.length === 1
            ? free[0]
            : free
                .toSorted((x, y) => (latest[y] ?? 0) - (latest[x] ?? 0))
                .find((position) => (latest[position] ?? 0) > 0);
        if (stand != null) {
          credit.set(orphan[0], [stand]);
        }
      }
      const overall = own.map(() => 0);
      const units = new Map<
        string,
        { turnout: number; shares: Array<number> }
      >();
      for (const [unit, unitLines] of Map.groupBy(
        source,
        (line) => line.unit,
      )) {
        let turnout = 0;
        const shares = own.map(() => 0);
        for (const line of unitLines) {
          turnout += line.votes;
          const positions =
            line.party == null ? [] : (credit.get(line.party) ?? []);
          for (const position of positions) {
            shares[position] =
              (shares[position] ?? 0) + line.votes / positions.length;
            overall[position] =
              (overall[position] ?? 0) + line.votes / positions.length;
          }
        }
        if (turnout > 0) {
          units.set(unit, {
            turnout,
            shares: shares.map((votes) => votes / turnout),
          });
        }
      }
      return {
        units,
        usable:
          all > 0 && overall.filter((votes) => votes >= 0.05 * all).length >= 2,
      };
    };
    const fromSeat = allot(seats);
    const chosen =
      fromSeat.usable || presidents.length === 0
        ? fromSeat.units
        : allot(presidents).units;
    const grouped = [...chosen].map(
      ([unit, value]) =>
        [
          unit,
          {
            shares: value.shares,
            turnout: fromSeat.units.get(unit)?.turnout ?? value.turnout,
          },
        ] as const,
    );
    let midterm = 0;
    let presidential = 0;
    for (const [unit, value] of grouped) {
      const ratio = prior.ratios.get(unit);
      if (ratio != null) {
        midterm += value.turnout * ratio;
        presidential += value.turnout;
      }
    }
    const fallback = presidential > 0 ? midterm / presidential : null;
    const baseline = grouped.map(([unit, value]) => {
      seen.add(unit);
      const traits = traitsOf.get(unit);
      return {
        unitId: unit,
        label: unitOf.get(unit)?.name ?? "",
        turnout: value.turnout,
        shares: value.shares.map((share) => Math.round(share * 1e6) / 1e6),
        ...(fallback != null && {
          expected: Math.round(
            value.turnout *
              Math.min(1.6, Math.max(0.3, prior.ratios.get(unit) ?? fallback)),
          ),
        }),
        ...(traits != null && { traits }),
      };
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
    const loose = own.some((choice) => choice.key.startsWith("party:"))
      ? []
      : own.filter(
          (choice) =>
            choice.kind !== "write_in" &&
            (choice.kind !== "answer" ||
              side(`answer:${choice.name.toLowerCase()}`) == null),
        );
    const top = Math.max(0, ...tally);
    const parts = row.path.split("/");
    const seat = parts.at(-1) ?? "";
    const isLocal = !house && !TOP.test(seat);
    const carried = new Set([
      ...counts.map((entry) => entry.unitId),
      ...baseline.map((entry) => entry.unitId),
    ]);
    const reported = new Set(
      counts.flatMap((entry) => {
        const scheme = unitOf.get(entry.unitId)?.scheme;
        return scheme == null ? [] : [scheme];
      }),
    );
    const source = mine.at(-1);
    const where = placed.get(row.id);
    const kind =
      where == null
        ? undefined
        : where.scheme === "us.judicial_district"
          ? "judicial"
          : JURISDICTIONS[where.kind];
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
      jurisdiction:
        where == null || kind == null
          ? null
          : { name: titles.full.get(where.polity) ?? "", kind },
      seats: where?.seats ?? 1,
      rule: rule(where?.rules),
      isSpecial: where?.special === true,
      needsMajority: where?.majority === true,
      isMeasure:
        own.some((choice) => choice.kind === "answer") &&
        own.every(
          (choice) => choice.kind === "answer" || choice.kind === "write_in",
        ),
      options: own.map((choice, at) => {
        const party = choice.key.startsWith("party:")
          ? choice.key.slice(6)
          : null;
        if (party == null) {
          const place = loose.indexOf(choice);
          return {
            id: choice.id,
            label: choice.name,
            person: choice.person,
            photo: choice.photo,
            party: null,
            ...(place !== -1 && {
              color: UNAFFILIATED[place % UNAFFILIATED.length],
            }),
            ...(choice.kind === "answer" && { isAnswer: true }),
            isWriteIn: choice.kind === "write_in",
            isIncumbent: sitting.has(choice.id),
          };
        }
        const name = partyNames.full.get(party) ?? choice.party ?? "";
        const color =
          choice.color ??
          ((tally[at] ?? 0) >= top * 0.05
            ? (UNAFFILIATED[
                Number.parseInt(party.slice(-4), 16) % UNAFFILIATED.length
              ] ?? "#8a8a8a")
            : "#8a8a8a");
        const shared = `${party}|${name}|${color}`;
        const entry = badges.get(shared) ?? {
          id: party,
          name,
          short:
            partyNames.short.get(party) ?? partyNames.full.get(party) ?? "",
          color,
        };
        badges.set(shared, entry);
        return {
          id: choice.id,
          label: choice.name,
          person: choice.person,
          photo: choice.photo,
          party: entry,
          isWriteIn: choice.kind === "write_in",
          isIncumbent: sitting.has(choice.id),
        };
      }),
      area: row.district,
      units: [...seen].flatMap((unit) => {
        const known = unitOf.get(unit);
        return known == null ||
          (isLocal && reported.size > 0 && !reported.has(known.scheme)) ||
          (known.parentId != null &&
            carried.has(known.parentId) &&
            !carried.has(unit))
          ? []
          : [known];
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
      projected: shown.results
        ? (() => {
            const call = calledBy.get(row.id);
            return call == null ? null : { option: call.option, at: call.at };
          })()
        : null,
      source: { publisher: source?.publisher ?? "", url: source?.url ?? null },
      feed:
        mine.length > 0 || final.length > 0
          ? "live"
          : (lapsed.get(parts[1] ?? "") ?? "live"),
    };
  });
  for (const race of built) {
    const of = race.rule?.minimum?.of;
    if (race.rule != null && (of === "ballots" || of === "governor")) {
      const governor = built.find(
        (other) => other.state === race.state && other.office === "governor",
      );
      const totals =
        governor?.certified?.totals ?? governor?.frames.at(-1)?.totals;
      let votes = 0;
      for (const value of totals ?? []) {
        votes += value;
      }
      race.rule.base = totals == null ? null : votes;
    }
  }
  const states = [...new Set(built.map((race) => race.state))].toSorted(
    (a, b) => a.localeCompare(b),
  );
  let newest: string | null = null;
  for (const frame of frames.rows) {
    if (newest == null || frame.at > newest) {
      newest = frame.at;
    }
  }
  const kept = detail !== "national";
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
    later: Object.fromEntries(
      [...lapsed].map(([state, feed]) => [
        state,
        {
          feed,
          name:
            titles.full.get(polityOf.get(state) ?? "") ?? state.toUpperCase(),
        },
      ]),
    ),
    races: built.map((race) =>
      kept ? race : { ...race, units: [], counts: [], baseline: [] },
    ),
  };
  return {
    election,
    shown,
    estimates: Object.fromEntries(
      (shown.forecasts ? built : []).map((race) => {
        const value = estimate({ race, at: null });
        return [race.id, kept ? value : { ...value, units: [] }];
      }),
    ),
  };
}

const SPREAD = {
  senate: /^senate(?:-special)?$/u,
  governor: /^governor$/u,
  house: /^house-(?:\d+|at-large)$/u,
};

const spread = new Map<string, Kept<Awaited<ReturnType<typeof spreadOut>>>>();

function countyRace(race: LiveRace): LiveRace {
  const units = race.units.flatMap((unit): Array<LiveUnit> =>
    unit.scheme === "us.county" && unit.areaId != null
      ? [{ ...unit, parentId: null }]
      : [],
  );
  const ids = new Set(units.map((unit) => unit.id));
  const latest = new Map<string, LiveUnitCount>();
  for (const row of race.counts) {
    const held = latest.get(row.unitId);
    if (ids.has(row.unitId) && (held == null || held.at <= row.at)) {
      latest.set(row.unitId, row);
    }
  }
  const last = race.frames.at(-1);
  return {
    ...race,
    units,
    counts: [...latest.values()],
    frames: last == null ? [] : [last],
    baseline: race.baseline.flatMap((row): Array<LiveBaseline> =>
      ids.has(row.unitId)
        ? [
            {
              unitId: row.unitId,
              label: row.label,
              turnout: row.turnout,
              shares: row.shares,
            },
          ]
        : [],
    ),
  };
}

function houseRace(races: Array<LiveRace>): LiveRace | null {
  const first = races[0];
  if (first == null) {
    return null;
  }
  const index = new Map<string, number>();
  const options: Array<LiveOption> = [];
  const slots = races.map((race) =>
    race.options.map((option) => {
      const key = option.party?.id ?? option.id;
      const at = index.get(key) ?? options.length;
      if (at === options.length) {
        index.set(key, at);
        options.push(
          option.party == null
            ? option
            : {
                ...option,
                id: key,
                label: option.party.name,
                person: null,
                photo: null,
                isIncumbent: false,
              },
        );
      }
      return at;
    }),
  );
  const units = new Map<string, LiveUnit>();
  const counts = new Map<string, LiveUnitCount>();
  const weights = new Map<
    string,
    { label: string; turnout: number; votes: Array<number> }
  >();
  const totals = options.map(() => 0);
  let at = "";
  for (const [position, race] of races.entries()) {
    const slot = slots[position] ?? [];
    const own = countyRace(race);
    const areaOf = new Map(
      own.units.map((unit) => [unit.id, unit.areaId ?? ""]),
    );
    for (const unit of own.units) {
      const areaId = unit.areaId ?? "";
      if (!units.has(areaId)) {
        units.set(areaId, { ...unit, id: areaId });
      }
    }
    for (const row of own.counts) {
      const areaId = areaOf.get(row.unitId) ?? "";
      const held = counts.get(areaId) ?? {
        unitId: areaId,
        at: row.at,
        votes: options.map(() => 0),
        reporting: null,
        expected: null,
      };
      for (const [option, value] of row.votes.entries()) {
        const to = slot[option] ?? 0;
        held.votes[to] = (held.votes[to] ?? 0) + value;
      }
      if (row.at > held.at) {
        held.at = row.at;
      }
      counts.set(areaId, held);
    }
    for (const row of own.baseline) {
      const areaId = areaOf.get(row.unitId) ?? "";
      const held = weights.get(areaId) ?? {
        label: row.label,
        turnout: 0,
        votes: options.map(() => 0),
      };
      held.turnout += row.turnout;
      for (const [option, share] of row.shares.entries()) {
        const to = slot[option] ?? 0;
        held.votes[to] = (held.votes[to] ?? 0) + share * row.turnout;
      }
      weights.set(areaId, held);
    }
    for (const [option, value] of (
      race.certified?.totals ??
      race.frames.at(-1)?.totals ??
      []
    ).entries()) {
      const to = slot[option] ?? 0;
      totals[to] = (totals[to] ?? 0) + value;
    }
    const last = race.frames.at(-1)?.at ?? "";
    if (last > at) {
      at = last;
    }
    for (const row of own.counts) {
      if (row.at > at) {
        at = row.at;
      }
    }
  }
  return {
    ...first,
    id: `house:${first.state}`,
    district: null,
    options,
    units: [...units.values()],
    counts: [...counts.values()],
    baseline: [...weights].flatMap(([areaId, held]): Array<LiveBaseline> =>
      held.turnout > 0
        ? [
            {
              unitId: areaId,
              label: held.label,
              turnout: held.turnout,
              shares: held.votes.map((value) => value / held.turnout),
            },
          ]
        : [],
    ),
    frames: at === "" ? [] : [{ at, totals, reporting: null, expected: null }],
    certified: null,
    projected: null,
  };
}

async function spreadOut(
  found: Awaited<ReturnType<typeof election>>,
  picked: Awaited<ReturnType<typeof races>>,
  chosen: keyof typeof SPREAD,
  shown: Shown,
  called: Awaited<ReturnType<typeof calls>>,
) {
  const result = await night(
    found,
    picked,
    "state",
    shown,
    called,
    `|${chosen}`,
  );
  const kept =
    chosen === "house"
      ? Map.groupBy(result.election.races, (race) => race.state)
          .values()
          .flatMap((group) => {
            const race = houseRace(group);
            return race == null ? [] : [race];
          })
          .toArray()
      : result.election.races.map((race) => countyRace(race));
  const areas = [
    ...new Set(
      kept.flatMap((race) =>
        race.units.flatMap((unit) =>
          unit.areaId == null ? [] : [unit.areaId],
        ),
      ),
    ),
  ];
  const framed = [
    ...new Set(
      kept.flatMap((race) =>
        TERRITORIES.has(race.state) ||
        race.state === "ak" ||
        race.state === "hi"
          ? []
          : race.units.flatMap((unit) =>
              unit.areaId == null ? [] : [unit.areaId],
            ),
      ),
    ),
  ];
  const map = await remember(
    `live:spread:v2:${found.date}:${chosen}:${String(areas.length)}`,
    async () =>
      shaped(areas, found.date, framed.length > 0 ? framed : areas, false),
  );
  const homes = new Map<string, { tail: string; turnout: number }>();
  if (chosen === "house") {
    for (const race of result.election.races) {
      const areaOf = new Map(
        race.units.map((unit) => [unit.id, unit.areaId ?? ""]),
      );
      for (const row of race.baseline) {
        const areaId = areaOf.get(row.unitId) ?? "";
        if (areaId !== "" && row.turnout > (homes.get(areaId)?.turnout ?? -1)) {
          homes.set(areaId, {
            tail: race.slug.split("/").at(-1) ?? "",
            turnout: row.turnout,
          });
        }
      }
    }
  }
  return {
    races: kept,
    map,
    homes: Object.fromEntries(
      [...homes].map(([areaId, home]) => [areaId, home.tail]),
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

export async function projections(path: string) {
  const found = await election(path);
  const picked = await races(found);
  const contests = picked.map((row) => row.id);
  return night(
    found,
    picked,
    found.path.split("/").length === 2 ? "national" : "state",
    { ...(await stored()), results: true, forecasts: true },
    await calls(contests),
  );
}

export const live = {
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
      const [stamp, shown, called] = await Promise.all([
        db.execute<{ key: string }>(sql`
          select coalesce(max(k), 0)::text || '|' || count(*)::text || '|' || coalesce(max(as_of)::text, '') as key
          from live.frame where contest_id = any(${sql.param(picked.map((row) => row.id))}::uuid[]) and status = 'active'
        `),
        switches(),
        calls(picked.map((row) => row.id)),
      ]);
      const slot = `${getLocale()}|${String(shown.results)}|${String(shown.forecasts)}|${found.path}|${found.rest}`;
      const key = shown.results
        ? `${stamp.rows[0]?.key ?? ""}|${called.map((row) => `${row.contest}:${row.option}:${row.at}`).join(",")}`
        : "";
      return settle(
        nights,
        slot,
        key,
        async () =>
          night(
            found,
            picked,
            found.rest !== ""
              ? "race"
              : found.path.split("/").length === 2
                ? "national"
                : "state",
            shown,
            called,
          ),
        256,
      );
    }),
  counties: base
    .input(
      v.objectAsync({
        path: Path,
        office: v.picklist(["senate", "governor", "house"]),
      }),
    )
    .handler(async ({ input }) => {
      const found = await election(input.path);
      if (found.rest !== "" || found.path.split("/").length !== 2) {
        throw new ORPCError("NOT_FOUND");
      }
      const picked = (await races(found)).filter((row) =>
        SPREAD[input.office].test(row.path.split("/", 4)[3] ?? ""),
      );
      const [stamp, shown, called] = await Promise.all([
        db.execute<{ key: string }>(sql`
          select coalesce(max(k), 0)::text || '|' || count(*)::text || '|' || coalesce(max(as_of)::text, '') as key
          from live.frame where contest_id = any(${sql.param(picked.map((row) => row.id))}::uuid[]) and status = 'active'
        `),
        switches(),
        calls(picked.map((row) => row.id)),
      ]);
      const slot = `${getLocale()}|${String(shown.results)}|${found.path}|${input.office}`;
      const key = `${stamp.rows[0]?.key ?? ""}|${called.map((row) => `${row.contest}:${row.option}:${row.at}`).join(",")}`;
      return settle(
        spread,
        slot,
        key,
        async () => spreadOut(found, picked, input.office, shown, called),
        64,
      );
    }),
  stamp: base
    .input(v.object({ path: v.string(), isRehearsal: v.optional(v.boolean()) }))
    .handler(async ({ input }) => {
      if (dev && input.isRehearsal === true) {
        return { newest: new Date().toISOString(), isCertified: false };
      }
      const userId = getRequestEvent().locals.user?.id;
      const slot = `${input.path}|${userId ?? ""}`;
      const recent = stamps.get(slot);
      if (recent != null && Date.now() - recent.at < STAMP_MS) {
        return recent.value;
      }
      const value = Promise.all([election(input.path), switches()]).then(
        async ([found, shown]) => newest(found.id, shown.results),
      );
      stamps.set(slot, { at: Date.now(), value });
      if (stamps.size > 256) {
        stamps.delete(stamps.keys().next().value ?? "");
      }
      value.catch(() => stamps.delete(slot));
      return value;
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
      const frame = await framing(
        scheme,
        [...new Set(values.map((row) => row.id))],
        chosen.date,
      );
      if (
        frame?.west == null ||
        frame.south == null ||
        frame.east == null ||
        frame.north == null
      ) {
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
