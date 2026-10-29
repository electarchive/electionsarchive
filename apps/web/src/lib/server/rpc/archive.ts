import type { Grouping, Match, Option, Relation, Side } from "@ea/analytics";
import type { SQL } from "drizzle-orm";
import { ancestry, compare } from "@ea/analytics";
import { openapi } from "@orpc/openapi";
import { call, ORPCError } from "@orpc/server";
import { sql } from "drizzle-orm";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import {
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import * as v from "valibot";
import { m } from "#lib/paraglide/messages.js";
import { os, remember } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { logos, withLogos } from "#lib/server/logos.js";
import {
  outside,
  parted,
  plane,
  projectionOf,
  spot,
} from "#lib/server/plane.js";
import { independent } from "#lib/party.js";

const stored = (tiles: string | null | undefined) =>
  tiles != null &&
  existsSync(path.resolve("../../data/plane", `${tiles}.pmtiles`))
    ? tiles
    : null;

const ORDER =
  "array['court_revised','recount','certified','official','preliminary','live','alternative']::stage[]";

const STAGES = sql.raw(ORDER);

export const latest = (set: string) =>
  sql.raw(
    `(${set}.is_default and ${set}.stage <> 'notional' and not exists (select from result_set later where later.contest_id = ${set}.contest_id and later.is_default and array_position(${ORDER}, later.stage) < array_position(${ORDER}, ${set}.stage)))`,
  );

const serial = (contest: string, election: string) =>
  sql.raw(
    `case when ${contest}.kind = 'ballot_question' then ${contest}.id::text else concat_ws(':', ${contest}.office_id, ${contest}.body_id, ${contest}.kind, ${election}.kind, case when ${contest}.previous_round_id is null then 1 else 1 + (with recursive back(id, depth) as (select ${contest}.previous_round_id, 1 union all select p.previous_round_id, back.depth + 1 from back join contest p on p.id = back.id where back.depth < 8) select count(*) from back join contest bc on bc.id = back.id join election be on be.id = bc.election_id where be.kind = ${election}.kind) end, ${contest}.party_scope_id) end`,
  );

const reached = (contest: string, election: string) =>
  sql.raw(
    `case when ${contest}.district_area_id is null or ${contest}.district_area_id in (select pt.area_id from polity_territory pt where pt.polity_id = ${election}.polity_id) then (select coalesce(array_agg(a.scheme_code || ':' || a.id::text order by a.scheme_code, a.id), '{}') from polity_territory pt join area_parent ap on ap.parent_id = pt.area_id join area a on a.id = ap.area_id where pt.polity_id = ${election}.polity_id and pt.valid @> ${election}.date_from and exists (select from result_set rs join result_rollup_sum x on x.result_set_k = rs.k and x.area_k = a.k where rs.contest_id = ${contest}.id and rs.is_default)) end`,
  );

const MEASURES = new Map([
  [1, "votes"],
  [8, "list_votes"],
  [11, "electoral_votes"],
  [12, "state_delegate_equivalents"],
  [27, "caucus_delegates"],
]);
const KINDS = ["shared", "each", "combined"] as const;
const COUNTED = sql`${sql.param(MEASURES.keys().toArray())}::int2[]`;
const CHANNELS = [
  "all",
  "election_day",
  "early",
  "postal",
  "abroad",
  "provisional",
  "mobile",
  "prison",
  "customs",
  "special_rules",
] as const;
const POOLS = [
  "abroad_mission",
  "customs_gate",
  "abroad_aggregate",
  "postal_district",
  "mobile",
  "prison",
  "aggregate",
  "standard",
] as const;
const POOLED = sql`
  u.area_relation = 'unlocated' and (u.kind <> 'standard' or u.parent_id is null)
`;
const POOLED_NAMES: Record<(typeof POOLS)[number], () => string> = {
  abroad_mission: m.places_pool_abroad,
  customs_gate: m.places_pool_customs,
  abroad_aggregate: m.places_pool_abroad_customs,
  postal_district: m.places_pool_postal,
  mobile: m.places_pool_mobile,
  prison: m.places_pool_prison,
  aggregate: m.places_pool_other,
  standard: m.places_pool_other,
};
const POOLED_CHANNELS: Partial<Record<number, () => string>> = {
  3: m.places_pool_postal,
  4: m.places_pool_abroad,
  6: m.places_pool_mobile,
  7: m.places_pool_prison,
  8: m.places_pool_customs,
  9: m.places_pool_special,
};
const PLACED = sql`
  u.area_relation = 'within' and u.kind <> 'standard'
`;
const POOL = sql`
  case when u.kind = 'aggregate' and exists (
    select from reporting_unit c where c.parent_id = u.id and c.kind = 'abroad_mission'
  ) then 'abroad_aggregate' else u.kind::text end
`;
const FACTS = {
  area: (set: SQL, area: SQL) =>
    sql`
      select r.result_set_k, r.area_k, r.option_k, r.measure, r.count_round, r.value, r.complete, r.channel
            from result_rollup r where r.result_set_k = ${set} and r.area_k = ${area} offset 0
    `,
  pool: (set: SQL, area: SQL, units: SQL) =>
    sql`
      select v.result_set_k, ${area} as area_k, v.option_k, v.measure, v.count_round, sum(v.value) as value, true as complete, v.channel
      from jsonb_array_elements_text(${units} -> (
        select e.polity_id::text from result_set s join election e on e.id = s.election_id where s.k = ${set}
      )) as uk(k)
      cross join lateral (select * from reporting_unit y where y.k = uk.k::int offset 0) u
      cross join lateral (select * from result_value x where x.result_set_k = ${set} and x.unit_k = u.k offset 0) v
      where (-(${area}) - 1) / 16 = 0 and ${POOLED} and ${POOL} = (${sql.param([...POOLS])}::text[])[(-(${area}) - 1) % 16 + 1]
        and not exists (
          select from reporting_unit c join result_value w on w.result_set_k = ${set} and w.unit_k = c.k where c.parent_id = u.id
          offset 0
        )
      group by 1, 2, 3, 4, 5, 8
      union all
      select v.result_set_k, ${area} as area_k, v.option_k, v.measure, v.count_round, sum(v.value) as value, true as complete, v.channel
      from area pa
      join reporting_unit u on u.area_id = pa.id and ${PLACED}
      join result_value v on v.result_set_k = ${set} and v.unit_k = u.k
      where pa.k = (-(${area}) - 1) / 16 and ${POOL} = (${sql.param([...POOLS])}::text[])[(-(${area}) - 1) % 16 + 1]
        and not exists (
          select from reporting_unit c join result_value w on w.result_set_k = ${set} and w.unit_k = c.k where c.parent_id = u.id
          offset 0
        )
      group by 1, 2, 3, 4, 5, 8
    `,
  unit: (set: SQL, unit: SQL) =>
    sql`
      select v.result_set_k, v.unit_k as area_k, v.option_k, v.measure, v.count_round, sum(v.value) as value, true as complete, v.channel
            from result_value v where v.result_set_k = ${set} and v.unit_k = ${unit}
            group by 1, 2, 3, 4, 5, 8
    `,
};
const CYCLE = sql`
  extract(year from e.date_from)::int - (
    e.kind = 'indirect' and extract(month from e.date_from) = 1 and exists (
      select from contest x join body b on b.id = x.body_id join polity y on y.id = b.polity_id
      where x.election_id = e.id and b.kind = 'electoral_college' and y.iso_code = 'US'
    )
  )::int
`;
const fused = (option: SQL, contest: SQL) => sql`
  (
    select ch.party_id from contest_option ch
    join result_set rs on rs.contest_id = ch.contest_id and ${latest("rs")}
    cross join lateral (select x.value from result_rollup x where x.result_set_k = rs.k and x.option_k = ch.k offset 0) x
    where ch.contest_id = ${contest} and ch.parent_option_id = ${option} and ch.party_id is not null
    group by ch.id, ch.party_id
    order by exists (
      select from tenure t join tenure_affiliation ta on ta.tenure_id = t.id
      where t.contest_id = ${contest} and ta.party_id = ch.party_id
    ) desc, max(x.value) desc, ch.id
    limit 1
  )
`;
const pooled = (list: SQL) => sql`
  exists (
    select from contest_option pl
    join contest_option ch on ch.parent_option_id = pl.id and ch.party_id is not null
      and ch.party_id is distinct from pl.party_id
    join outcome y on y.option_id = ch.id and y.kind = 'elected' and y.candidacy_id is null
    join result_set ry on ry.id = y.result_set_id and ${latest("ry")}
    where pl.id = ${list} and pl.kind = 'list'
    offset 0
  )
`;
const stray = (candidacy: SQL, option: SQL, contest: SQL) => sql`
  (
    select case
      when k.party_scope_id is null then false
      when not exists (
        select from candidacy oc
        join contest_option oo on oo.id = oc.option_id
        join contest ok on ok.id = oo.contest_id
        join election oe on oe.id = ok.election_id
        where oc.person_id = (select person_id from candidacy where id = ${candidacy}) and oc.id <> ${candidacy}
          and extract(year from oe.date_from) = extract(year from e.date_from)
          and (
            coalesce(ok.party_scope_id, oo.party_id) <> k.party_scope_id
            or ok.party_scope_id is null and oc.is_independent
          )
      ) then false
      when exists (
        select from outcome x join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
        where x.option_id = ${option} and x.kind in ('elected', 'won', 'advanced', 'tied')
      ) then false
      else coalesce((
        select v.mine / nullif(v.total, 0) from result_set rs
        cross join lateral (
          select sum(x.value) filter (where x.option_k = (select k from contest_option where id = ${option}))::float8 as mine,
            sum(x.value)::float8 as total
          from result_rollup x
          where x.result_set_k = rs.k and x.option_k <> 0 and x.measure = any(${COUNTED})
            and x.area_k = (
              select a.k from area a where a.id = coalesce(k.district_area_id, (
                select pt.area_id from polity_territory pt where pt.polity_id = e.polity_id and pt.valid @> e.date_from limit 1
              ))
            )
          offset 0
        ) v
        where rs.contest_id = k.id and ${latest("rs")}
        limit 1
      ), 0) < 0.05
    end
    from contest k join election e on e.id = k.election_id
    where k.id = ${contest}
  )
`;
const paint = (party: SQL, elections: SQL, date: SQL) => sql`
  coalesce(
    (select pc.color from party_color pc where pc.party_id = ${party} and pc.valid @> ${date}),
    (select pc.color from party_relation r
      left join entity_merge fm on fm.entity_id = r.from_party_id
      left join entity_merge tm on tm.entity_id = r.to_party_id
      join party_color pc on pc.party_id = coalesce(tm.into_id, r.to_party_id) and pc.valid @> ${date}
      where r.kind = 'list_vehicle_for' and coalesce(fm.into_id, r.from_party_id) = ${party} and r.election_id in (${elections})
      limit 1),
    (select pc.color from party_affiliation a
      left join entity_merge bm on bm.entity_id = a.parent_party_id
      join party_color pc on pc.party_id = coalesce(bm.into_id, a.parent_party_id) and pc.valid @> ${date}
      where a.party_id = ${party} and a.kind = 'regional_branch' and a.valid @> ${date}
      limit 1)
  )
`;

const holding = (tenure: string, day: SQL) => {
  const t = sql.raw(tenure);
  return sql`(${t}.from_date is null or ${t}.from_date < ${day})
    and (${t}.to_date is null or ${t}.to_date >= ${day}
      or (${t}.ended_via in ('term_end', 'dissolved') and ${t}.to_date >= ${day} - 120))`;
};

const covered = (contests: SQL) => sql`
  with tk as (
    select c.id, coalesce(c.office_id, pc.office_id) as office_id, c.body_id, e.date_from as d
    from contest c join election e on e.id = c.election_id
    left join contest pc on pc.id = c.allocation_parent_id
    where c.id in (${contests})
  ),
  tp as (select distinct office_id, body_id, d from tk),
  th as (
    select p.office_id, p.body_id, p.d from tp p
    join tenure t on t.office_id = p.office_id and ${holding("t", sql`p.d`)}
    union
    select p.office_id, p.body_id, p.d from tp p
    join tenure t on t.body_id = p.body_id and p.office_id is null and ${holding("t", sql`p.d`)}
  )
  select tk.id from tk join th on th.office_id is not distinct from tk.office_id
    and th.body_id is not distinct from tk.body_id and th.d = tk.d
`;

const tenured = (person: SQL, contest: SQL) => sql`exists (
  select from contest hc join election he on he.id = hc.election_id
  left join contest hp on hp.id = hc.allocation_parent_id
  join tenure ht on ht.person_id in (
    select ${person} union all select hm.entity_id from entity_merge hm where hm.into_id = ${person}
  )
  where hc.id = ${contest}
    and ${holding("ht", sql`he.date_from`)}
    and (ht.office_id = coalesce(hc.office_id, hp.office_id)
      or (coalesce(hc.office_id, hp.office_id) is null and ht.body_id = hc.body_id))
)`;

const abbreviate = (party: SQL, date: SQL) => sql`
  (select a.text from name a
    left join lateral (
      select f.text, f.is_preferred, f.valid from name f
      where f.entity_id = a.entity_id and f.kind in ('official', 'english')
      order by coalesce(f.valid @> coalesce(${date}, current_date), false) desc,
        coalesce(f.valid @> coalesce(${date}, current_date) and not (lower_inf(f.valid) and upper_inf(f.valid)), false) desc, f.is_preferred desc,
        (f.lang = a.lang) desc, (f.kind = 'official') desc, f.recorded_from desc, f.id
      limit 1
    ) f on true
    where a.entity_id = ${party} and a.kind in ('short', 'abbreviation')
      and not (
        f.is_preferred is false and not (lower_inf(f.valid) and upper_inf(f.valid)) and lower_inf(a.valid) and upper_inf(a.valid)
        and exists (select from name p where p.entity_id = a.entity_id and p.kind in ('official', 'english') and p.is_preferred)
      )
    order by (a.kind = 'short') desc, coalesce(a.valid @> coalesce(${date}, current_date), false) desc,
      (lower_inf(a.valid) and upper_inf(a.valid)), a.is_preferred desc, (a.lang = 'en') desc,
      coalesce(
        upper(array_to_string(array(
          select left(w, 1) from regexp_split_to_table(f.text, '[^[:alnum:]]+') w where w <> ''
        ), ''))
        like array_to_string(regexp_split_to_array(regexp_replace(upper(a.text), '[^[:alnum:]]', '', 'g'), ''), '%') || '%',
        false) desc,
      length(a.text), a.text, a.recorded_from desc, a.id
    limit 1)
`;

const sisters = (roots: Array<string>, day: string) => sql`
  with recursive tree(id) as (
    select unnest(${sql.param(roots)}::uuid[])
    union all
    select c.id from contest c join tree on c.allocation_parent_id = tree.id
  ),
  lines as materialized (
    select t.id as contest, o.id as option,
      coalesce(bm.into_id, b.parent_party_id, pm.into_id, o.party_id) as party
    from tree t
    cross join lateral (
      select o.id, o.party_id from contest_option o where o.contest_id = t.id and o.party_id is not null offset 0
    ) o
    left join entity_merge pm on pm.entity_id = o.party_id
    left join party_affiliation b on b.party_id = coalesce(pm.into_id, o.party_id)
      and b.kind = 'regional_branch' and b.valid @> ${day}::date
    left join entity_merge bm on bm.entity_id = b.parent_party_id
    where not exists (select from contest k where k.allocation_parent_id = t.id)
  ),
  reach as (
    select l.party, array_agg(distinct l.contest) as contests, count(distinct l.contest) as n
    from lines l
    where l.party in (
      select w.party from lines w
      cross join lateral (
        select from outcome x join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
        where x.option_id = w.option and x.kind in ('elected', 'won')
        limit 1
      ) won
    )
    group by l.party
  ),
  pairs as (
    select a.party as a, b.party as b
    from reach a join reach b on a.party < b.party
    where a.n + b.n = (select count(distinct contest) from lines)
      and not a.contests && b.contests
  )
  select 'party:' || p.a as from, 'party:' || p.b as to
  from pairs p
  where (select count(*) from pairs q where p.a in (q.a, q.b)) = 1
    and (select count(*) from pairs q where p.b in (q.a, q.b)) = 1
`;

const Link = v.object({ name: v.string(), path: v.nullable(v.string()) });

const PartyRef = v.object({
  id: v.string(),
  label: v.string(),
  short: v.nullable(v.string()),
  color: v.nullable(v.string()),
});

const Winner = v.object({
  label: v.string(),
  person: v.nullable(v.string()),
  party: v.nullable(v.string()),
  color: v.nullable(v.string()),
  share: v.nullable(v.number()),
});

const Step = v.object({
  id: v.string(),
  path: v.nullable(v.string()),
  date: v.nullable(v.string()),
  until: v.nullable(v.string()),
  party: v.nullable(PartyRef),
  isCurrent: v.boolean(),
  isMinor: v.boolean(),
  winners: v.array(Winner),
  runoff: v.nullable(
    v.object({
      id: v.string(),
      path: v.nullable(v.string()),
      date: v.nullable(v.string()),
      isCurrent: v.boolean(),
      winners: v.array(Winner),
    }),
  ),
  local: v.nullable(
    v.object({
      path: v.nullable(v.string()),
      place: v.string(),
      isCurrent: v.boolean(),
    }),
  ),
});

const GROUPS = [
  "owner",
  "governor",
  "legislature",
  "regional_office",
  "national_measure",
  "regional_measure",
  "local",
  "local_measure",
] as const;

const Unreconciled = v.object({
  reason: v.picklist(["drifted", "overcounted", "no official total"]),
  counted: v.number(),
  official: v.nullable(v.number()),
  drifted: v.array(
    v.object({ party: v.string(), counted: v.number(), official: v.number() }),
  ),
});

const ResultSet = v.object({
  stage: v.string(),
  status: v.string(),
  completeness: v.nullable(v.number()),
  isDefault: v.boolean(),
  unreconciled: v.optional(v.nullable(Unreconciled)),
  source: v.object({
    kind: v.string(),
    publisher: v.string(),
    title: v.string(),
    url: v.nullable(v.string()),
    license: v.nullable(v.string()),
    attribution: v.nullable(v.string()),
  }),
});

const LogoRef = v.object({
  src: v.string(),
  width: v.number(),
  height: v.number(),
  mark: v.string(),
  shape: v.picklist(["mark", "wordmark"]),
  isPlate: v.boolean(),
  isLight: v.boolean(),
  license: v.string(),
  artist: v.nullable(v.string()),
  source: v.string(),
});

const LogoMap = v.record(v.string(), LogoRef);

const Photo = v.nullable(
  v.object({
    src: v.string(),
    license: v.string(),
    artist: v.nullable(v.string()),
    source: v.string(),
    taken: v.nullable(
      v.object({ from: v.nullable(v.number()), to: v.nullable(v.number()) }),
    ),
  }),
);

const Cell = v.nullable(
  v.object({
    label: v.string(),
    person: v.nullable(v.string()),
    party: v.nullable(v.string()),
    votes: v.number(),
    share: v.number(),
    outcome: v.nullable(v.string()),
    note: v.optional(v.nullable(v.string())),
    isDeclined: v.optional(v.boolean()),
    isIndependent: v.optional(v.boolean()),
    isIncumbent: v.optional(v.boolean()),
    isWriteIn: v.optional(v.boolean()),
    ballots: v.optional(v.number()),
    seats: v.nullable(v.number()),
    lines: v.array(v.object({ label: v.string(), votes: v.number() })),
  }),
);

const FINE = v.pipe(
  v.number(),
  v.transform((value) => Math.round(value * 1e5) / 1e5),
);

const PlaceRow = v.object({
  k: v.number(),
  turnout: v.optional(v.nullable(v.tuple([v.number(), v.number()]))),
  turnedBefore: v.optional(v.nullable(FINE)),
  at: v.optional(v.nullable(v.tuple([FINE, FINE]))),
  name: v.string(),
  path: v.nullable(v.string()),
  contest: v.boolean(),
  isRemainder: v.boolean(),
  valid: v.number(),
  shares: v.array(FINE),
  absent: v.optional(v.array(v.number())),
  leader: v.nullable(v.number()),
  held: v.optional(v.nullable(v.array(v.tuple([v.number(), v.number()])))),
  margin: v.nullable(FINE),
  earlier: v.nullable(v.array(FINE)),
  prior: v.nullable(v.number()),
  redrawn: v.optional(v.number()),
  within: v.optional(v.number()),
  seats: v.optional(v.nullable(v.number())),
  isCounted: v.optional(v.boolean()),
  isTally: v.optional(v.boolean()),
  declared: v.optional(
    v.nullable(
      v.object({
        kind: v.picklist(["elected", "won"]),
        isTacit: v.boolean(),
        label: v.string(),
        color: v.nullable(v.string()),
        person: v.nullable(v.string()),
        party: v.nullable(v.string()),
      }),
    ),
  ),
  isRecorded: v.optional(v.boolean()),
  isPool: v.optional(v.boolean()),
  status: v.optional(v.string()),
});

const Places = v.nullable(
  v.object({
    levels: v.array(
      v.object({ code: v.string(), noun: v.string(), count: v.number() }),
    ),
    level: v.string(),
    legend: v.array(
      v.object({
        key: v.string(),
        label: v.string(),
        short: v.nullable(v.string()),
        color: v.nullable(v.string()),
        person: v.nullable(v.string()),
        party: v.nullable(v.string()),
      }),
    ),
    rows: v.array(PlaceRow),
    districts: v.optional(v.array(PlaceRow)),
    total: v.optional(v.number()),
    noted: v.optional(
      v.array(
        v.object({ k: v.number(), name: v.string(), status: v.string() }),
      ),
    ),
    isOpening: v.optional(v.boolean()),
    pending: v.optional(v.array(v.string())),
    sources: v.array(v.string()),
    unreconciled: v.optional(
      v.array(v.object({ ...Unreconciled.entries, publisher: v.string() })),
    ),
    estimated: v.optional(v.boolean()),
    context: v.optional(
      v.object({ date: v.string(), path: v.nullable(v.string()) }),
    ),
  }),
);

const Sketch = v.object({
  tiles: v.nullable(v.string()),
  own: v.nullable(
    v.object({
      tiles: v.string(),
      k: v.number(),
      parted: v.optional(
        v.object({ isOnly: v.boolean(), ks: v.array(v.number()) }),
      ),
    }),
  ),
  parted: v.optional(
    v.object({ isOnly: v.boolean(), ks: v.array(v.number()) }),
  ),
  nested: v.optional(
    v.nullable(v.object({ tiles: v.string(), ks: v.array(v.number()) })),
  ),
  frame: v.object({
    west: v.number(),
    south: v.number(),
    east: v.number(),
    north: v.number(),
  }),
  blank: v.optional(
    v.array(
      v.object({
        k: v.number(),
        name: v.string(),
        isHeld: v.boolean(),
        isOutside: v.boolean(),
        isPending: v.boolean(),
      }),
    ),
  ),
  approximate: v.array(v.number()),
  shaped: v.optional(v.nullable(v.number())),
  credits: v.optional(v.nullable(v.array(v.string()))),
  outlying: v.optional(
    v.array(
      v.object({
        k: v.number(),
        code: v.optional(v.string()),
        group: v.picklist(["territory", "overseas", "abroad"]),
      }),
    ),
  ),
});

type Found = {
  id: string;
  kind: string;
  indirect: boolean;
  isElectoral: boolean;
  series: string;
  root: string;
  election: string;
  polity: string;
  date: string | null;
  year: number | null;
  status: string;
  isRemainder: boolean;
  place: { id: string; k: number } | null;
};

function depth(path: string) {
  return path.split("/").length;
}

function key(row: { party: string | null; person: string | null }) {
  return row.party == null
    ? `person:${row.person ?? ""}`
    : `party:${row.party}`;
}

function segment(text: string) {
  return (
    text
      .replaceAll(/ß/giu, "ss")
      .replaceAll("ı", "i")
      .replaceAll(/ł/giu, "l")
      .replaceAll(/ø/giu, "o")
      .replaceAll(/æ/giu, "ae")
      .replaceAll(/œ/giu, "oe")
      .replaceAll(/[đð]/giu, "d")
      .replaceAll(/þ/giu, "th")
      .toLowerCase()
      .normalize("NFKD")
      .replaceAll(/\p{M}/gu, "")
      .replaceAll(/[^a-z0-9]+/gu, "-")
      .replaceAll(/^-|-$/gu, "") || "unnamed"
  );
}

export async function representative(election: string, anchor: string) {
  const found = await db.execute<{ id: string; owner: string }>(sql`
    with recursive down(id) as (
      select ${election}::uuid
      union all
      select k.id from election k join down on k.parent_id = down.id
    )
    select (array_agg(c.id order by c.id))[1]::text as id, coalesce(c.body_id, c.office_id) as owner
    from down join contest c on c.election_id = down.id
    where not exists (select from contest pr join down d on d.id = pr.election_id where pr.id = c.previous_round_id)
      and c.allocation_parent_id is null
      and not c.notional and c.district_area_id is not null and coalesce(c.body_id, c.office_id) is not null
    group by coalesce(c.body_id, c.office_id), c.kind, c.term
    having count(*) >= 3
    order by count(*) desc
  `);
  const labels = await names(found.rows.map((row) => row.owner));
  const bare = anchor.replace(/-\d+$/u, "");
  const named = found.rows.map((row) => ({
    id: row.id,
    anchor: segment(labels.full.get(row.owner) ?? ""),
  }));
  return (
    named.find((row) => row.anchor === anchor) ??
    named.find((row) => row.anchor === bare)
  )?.id;
}

export async function names(
  ids: Array<string>,
  on: string | null = null,
  common: Array<string> = [],
) {
  const result = await db.execute<{
    entity_id: string;
    kind: string;
    text: string;
  }>(sql`
    (select n.entity_id, n.kind, coalesce(to_char(d.date, 'FMMonth FMDD, ') || n.text, n.text) as text
    from (
      select distinct on (entity_id) entity_id, kind::text, text
      from name
      where entity_id = any(${sql.param(ids)}::uuid[]) and kind in ('official', 'english')
      order by entity_id, coalesce(valid @> coalesce(${on}::date, current_date), false) desc,
        coalesce(valid @> coalesce(${on}::date, current_date) and not (lower_inf(valid) and upper_inf(valid)), false) desc,
        coalesce(kind = 'english' and script = 'Latn' and entity_id = any(${sql.param(common)}::uuid[]), false) desc, is_preferred desc,
        (lang = 'en') desc, (kind = 'official') desc, recorded_from desc, id
    ) n
    left join lateral (
      select e.date_from as date from election e
      where e.id = n.entity_id and e.date_precision = 'day'
        and n.text like extract(year from e.date_from)::int || ' %'
        and exists (
          select from election o
          join name m on m.entity_id = o.id and m.is_preferred and m.lang = 'en'
          where o.polity_id = e.polity_id and o.id <> e.id and o.status::text <> 'annulled'
            and o.date_from >= date_trunc('year', e.date_from)::date
            and o.date_from < (date_trunc('year', e.date_from) + interval '1 year')::date
            and o.date_from <> e.date_from
            and regexp_replace(m.text, '^[A-Z][a-z]+ [0-9]{1,2}, ', '') = n.text
        )
    ) d on true)
    union all
    select i.id, 'abbreviation', short.text
    from (select distinct unnest(${sql.param(ids)}::uuid[]) as id) i
    cross join lateral (select ${abbreviate(sql`i.id`, sql`${on}::date`)} as text) short
    where short.text is not null
  `);
  const full = new Map<string, string>();
  const short = new Map<string, string>();
  for (const row of result.rows) {
    (row.kind === "abbreviation" ? short : full).set(row.entity_id, row.text);
  }
  return { full, short };
}

export async function paths(ids: Array<string>) {
  const result = await db.execute<{ entity_id: string; path: string }>(sql`
    select entity_id, path from slug where is_current and entity_id = any(${sql.param(ids)}::uuid[])
  `);
  return new Map(result.rows.map((row) => [row.entity_id, row.path]));
}

const COURT = /\b(?:Judge|Justice|Court|Surrogate|Magistrate)\b/u;

async function breadcrumbs(polity: string, keep: string | null = null) {
  const result = await db.execute<{ id: string }>(sql`
    with recursive chain(id, depth) as (
      select ${polity}::uuid, 0
      union all
      select p.parent_id, chain.depth + 1 from polity_parent p join chain on p.polity_id = chain.id where chain.depth < 8
    ),
    county as (
      select id from (
        select c.id, st_contains(cg.geom, coalesce(g.label_point, st_pointonsurface(g.geom))) as holds,
          count(*) filter (where st_contains(g.geom, coalesce(cg.label_point, st_pointonsurface(cg.geom)))) over () as inside
        from polity y
        join polity_parent pp on pp.polity_id = y.id
        join polity s on s.id = pp.parent_id and s.kind = 'federal_unit'
        join polity_territory t on t.polity_id = y.id and upper_inf(t.valid)
        join area_geometry g on g.area_id = t.area_id and upper_inf(g.valid)
        join polity_parent cp on cp.parent_id = pp.parent_id
        join polity c on c.id = cp.polity_id and c.kind = 'county'
        join polity_territory ct on ct.polity_id = c.id and upper_inf(ct.valid)
        join area_geometry cg on cg.area_id = ct.area_id and upper_inf(cg.valid)
        where y.id = ${polity}::uuid and y.kind in ('municipality', 'school_district', 'special_district')
          and cg.geom && g.geom
      ) x
      where holds and inside < 2
      limit 1
    )
    select id from (
      select chain.id, chain.depth::numeric as depth from chain join polity y on y.id = chain.id
      where chain.id = ${polity}::uuid or y.kind <> 'supranational'
        or ${keep}::text = 'all'
        or exists (
          select from election e join election up on up.id = e.parent_id
          where e.id::text = ${keep}::text and up.polity_id = chain.id
        )
      union all
      select id, 0.5 from county
    ) x
    group by id order by max(depth) desc
  `);
  const ids = result.rows.map((row) => row.id);
  const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
  return ids.map((id) => ({
    name: labels.full.get(id) ?? "",
    path: slugs.get(id) ?? null,
  }));
}

async function find(id: string) {
  const found = await db.execute<Found>(sql`
    with recursive up(id, parent) as (
      select id, allocation_parent_id from contest where id = ${id}::uuid
      union all
      select c.id, c.allocation_parent_id from contest c join up on c.id = up.parent
    )
    select c.id, c.kind::text as kind, ${serial("r", "e")} as series,
      r.id as root, e.id as election, e.kind = 'indirect' as indirect,
      exists (select from body b where b.id = r.body_id and b.kind = 'electoral_college') as "isElectoral",
      e.polity_id as polity, e.date_from::text as date,
      ${CYCLE} as year, e.status::text as status, r.term is not distinct from 'remainder' as "isRemainder",
      case when a.id is not null then json_build_object('id', a.id, 'k', a.k) end as place
    from contest c
    join up on up.parent is null
    join contest r on r.id = up.id
    join election e on e.id = c.election_id
    left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
    left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
    where c.id = ${id}::uuid
  `);
  const contest = found.rows[0];
  if (contest == null) {
    throw new ORPCError("NOT_FOUND");
  }
  return contest;
}

async function children(
  contest: string | Array<string>,
  area: { id: string; k: number },
  isRoot: boolean,
  date: string,
  target?: number,
  isDeep = false,
) {
  const sets = await db.execute<{ k: number }>(sql`
    with recursive grp(id) as (
      select unnest(${sql.param([contest].flat())}::uuid[])
      union all
      select c.id from contest c join grp on c.allocation_parent_id = grp.id
    )
    select rs.k from grp cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs
  `);
  const ks = sets.rows.map((row) => row.k);
  if (ks.length === 0) {
    return [];
  }
  const counted = sql`(
    exists (
      select from unnest(${sql.param(ks)}::int[]) as s(k)
      join result_rollup_sum r on r.result_set_k = s.k and r.area_k = a.k and r.measure = any(${COUNTED})
    )
    or exists (
      select from unnest(${sql.param(ks)}::int[]) as s(k)
      join result_rollup_unit u on u.result_set_k = s.k and u.area_k = a.k
      cross join lateral (
        select from result_value v
        where v.result_set_k = u.result_set_k and v.unit_k = u.unit_k and v.measure = any(${COUNTED})
        limit 1
      ) v
    )
  )`;
  let found = await db.execute<{ id: string; k: number; scheme: string }>(sql`
    select a.id, a.k, a.scheme_code as scheme
    from area_parent p join area a on a.id = p.area_id
    where p.parent_id = ${area.id}::uuid and p.valid @> ${date}::date and ${counted}
  `);
  const lone = Map.groupBy(found.rows, (row) => row.scheme)
    .values()
    .filter((group) => group.length === 1)
    .flatMap((group) => group.map((row) => row.id))
    .toArray();
  const deeper = new Set<string>();
  if (lone.length > 0) {
    const through = await db.execute<{
      id: string;
      k: number;
      scheme: string;
    }>(sql`
      select a.id, a.k, a.scheme_code as scheme
      from area_parent p join area a on a.id = p.area_id
      where p.parent_id = any(${sql.param(lone)}::uuid[]) and p.valid @> ${date}::date
        and a.scheme_code <> all(${sql.param(found.rows.map((row) => row.scheme))}::text[])
        and ${counted}
    `);
    for (const row of through.rows) {
      deeper.add(row.id);
      found.rows.push(row);
    }
  }
  if (isRoot && found.rows.length < 2) {
    found = await db.execute<{ id: string; k: number; scheme: string }>(sql`
      with recursive anc(id) as (
        select ${area.id}::uuid
        union
        select p.parent_id from area_parent p join anc on p.area_id = anc.id
      ),
      me as materialized (
        select geom from area_geometry where area_id = ${area.id}::uuid
        order by valid @> ${date}::date desc, upper(valid) desc nulls first limit 1
      )
      select a.id, a.k, a.scheme_code as scheme
      from (
        select r.area_k, coalesce(bool_or(r.value <> 0), false) as voted
        from unnest(${sql.param(ks)}::int[]) as s(k) cross join lateral (
          select x.area_k, x.value from result_rollup x where x.result_set_k = s.k and x.measure = any(${COUNTED}) offset 0
        ) r
        group by r.area_k
      ) r
      join area a on a.k = r.area_k
      where a.id not in (select id from anc)
        and (
          r.voted
          or not exists (select from me)
          or not exists (select from area_geometry g where g.area_id = a.id)
          or exists (
            select from me cross join lateral (
              select g.geom from area_geometry g where g.area_id = a.id
              order by g.valid @> ${date}::date desc, upper(g.valid) desc nulls first limit 1
            ) g
            where ST_Area(ST_Intersection(g.geom, me.geom)) >= 0.01 * ST_Area(g.geom)
          )
        )
    `);
  }
  const settled = found.rows.some(
    (row) => row.k === target && !deeper.has(row.id),
  );
  const linked = settled
    ? { rows: [] }
    : await db.execute<{ yes: boolean }>(sql`
        select exists (select from reporting_unit_area m where m.area_id = ${area.id}::uuid) as yes
      `);
  const joined = linked.rows[0]?.yes
    ? await db.execute<{ id: string; k: number; scheme: string }>(sql`
      with r as materialized (
        select distinct u.area_k from unnest(${sql.param(ks)}::int[]) as s(k)
        join result_rollup_unit u on u.result_set_k = s.k
      )
      select a.id, a.k, a.scheme_code as scheme
      from r join area a on a.k = r.area_k
      where (select count(*) from r) <= 25000
        and a.id <> ${area.id}::uuid
        and a.scheme_code <> all(${sql.param([...new Set(found.rows.map((row) => row.scheme))])}::text[])
        and exists (
          select from reporting_unit ru
          cross join lateral (
            select from reporting_unit_area m
            where m.unit_id = ru.id and m.area_id = ${area.id}::uuid and m.valid @> ${date}::date
            limit 1
          ) m
          where ru.area_id = a.id
        )
    `)
    : { rows: [] };
  const abroad =
    isRoot &&
    !settled &&
    !found.rows.some((row) => row.scheme === "world.country")
      ? await db.execute<{ id: string; k: number; scheme: string }>(sql`
          select a.id, a.k, a.scheme_code as scheme from area a
          where a.scheme_code = 'world.country' and ${counted}
        `)
      : { rows: [] };
  const members = new Set(
    [...joined.rows, ...abroad.rows].map((row) => row.id),
  );
  found.rows.push(...joined.rows, ...abroad.rows);
  if (found.rows.length === 0) {
    return [];
  }
  const unlinked = await db.execute<{ id: string; k: number; scheme: string }>(
    sql`
      select distinct a.id, a.k, a.scheme_code as scheme
      from contest c
      join result_set rs on rs.contest_id = c.id and rs.k = any(${sql.param(ks)}::int[])
      cross join lateral (
        select r.area_k from result_rollup_sum r where r.result_set_k = rs.k and r.measure = any(${COUNTED})
        union
        select u.area_k from result_rollup_unit u where u.result_set_k = rs.k
      ) r
      join area a on a.k = r.area_k
        and a.scheme_code = any(${sql.param([...new Set(found.rows.map((row) => row.scheme))])}::text[])
      where c.district_area_id = ${area.id}::uuid
        and a.k <> all(${sql.param(found.rows.map((row) => row.k))}::int[])
        and not exists (select from area_parent p where p.area_id = a.id and p.valid @> ${date}::date)
    `,
  );
  found.rows.push(...unlinked.rows);
  const tops = found.rows.filter(
    (row) => !members.has(row.id) && !deeper.has(row.id),
  );
  const below =
    isDeep && isRoot && tops.length > 1 && tops.length <= 200
      ? await db.execute<{
          id: string;
          k: number;
          scheme: string;
          parent: string;
        }>(sql`
          select a.id, a.k, a.scheme, a.parent from (
            select a.id, a.k, a.scheme_code as scheme, p.parent_id as parent
            from area_parent p join area a on a.id = p.area_id
            where p.parent_id = any(${sql.param(tops.map((row) => row.id))}::uuid[])
              and p.valid @> ${date}::date
              and a.scheme_code in (
                select s.code from scheme s
                where s.kind = 'administrative' and s.rank = (
                  select max(x.rank) + 1 from scheme x
                  where x.code = any(${sql.param([...new Set(tops.map((row) => row.scheme))])}::text[])
                )
              )
              and exists (select from boundary_set bs where bs.scheme_code = a.scheme_code and bs.valid @> ${date}::date)
            offset 0
          ) a
          where ${counted}
        `)
      : { rows: [] };
  const deep = Map.groupBy(below.rows, (row) => row.scheme)
    .values()
    .filter((rows) => rows.length > tops.length && rows.length <= 10_000)
    .toArray()
    .flat();
  const [labels, schemes] = await Promise.all([
    names(
      [...found.rows, ...deep].map((row) => row.id),
      date,
    ),
    db.execute<{ code: string; noun: string; kind: string; rank: number }>(sql`
      select code, noun, kind::text, rank from scheme
      where code = any(${sql.param([...new Set([...found.rows, ...deep].map((row) => row.scheme))])}::text[])
    `),
  ]);
  const scheme = new Map(schemes.rows.map((row) => [row.code, row]));
  const taken = new Map<string, number>();
  const shaped = (
    row: { id: string; k: number; scheme: string },
    prefix: string,
  ) => {
    const name = labels.full.get(row.id) ?? "";
    const base = `${prefix}${segment(name)}`;
    const seen = (taken.get(base) ?? 0) + 1;
    taken.set(base, seen);
    return {
      id: row.id,
      k: row.k,
      scheme: row.scheme,
      name,
      segment: seen === 1 ? base : `${base}-${seen}`,
      noun: scheme.get(row.scheme)?.noun ?? "",
      kind: scheme.get(row.scheme)?.kind ?? "",
      rank: scheme.get(row.scheme)?.rank ?? 0,
      isMember: members.has(row.id),
      isDeep: prefix !== "",
    };
  };
  const listed = found.rows
    .toSorted(
      (a, b) =>
        Number(deeper.has(a.id)) - Number(deeper.has(b.id)) ||
        Number(members.has(a.id)) - Number(members.has(b.id)) ||
        a.k - b.k,
    )
    .map((row) => shaped(row, ""));
  const prefixes = new Map(listed.map((row) => [row.id, `${row.segment}/`]));
  return [
    ...listed,
    ...deep
      .toSorted((a, b) => a.k - b.k)
      .map((row) => shaped(row, prefixes.get(row.parent) ?? "")),
  ].toSorted((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true }),
  );
}

async function descend(
  contest: Found,
  unit: string | undefined,
  roots: Array<string> = [contest.id],
) {
  const chain: Awaited<ReturnType<typeof children>> = [];
  const pieces = (unit ?? "").split("/").filter((piece) => piece !== "");
  if (pieces.length > 0 && contest.place == null) {
    throw new ORPCError("NOT_FOUND");
  }
  let current = contest.place ?? { id: "", k: 0 };
  for (const piece of pieces) {
    const kids = await children(
      roots,
      current,
      chain.length === 0,
      contest.date ?? "infinity",
    );
    const kid = kids.find((entry) => entry.segment === piece);
    if (kid == null) {
      throw new ORPCError("NOT_FOUND");
    }
    chain.push(kid);
    current = kid;
  }
  return chain;
}

const ABROAD: Record<string, string> = {
  br: "Exterior",
  fr: "Français établis hors de France",
  it: "Estero",
  tr: "Yurt dışı",
};

const UNAFFILIATED = [
  "#0e8f7e",
  "#8250c4",
  "#b5651d",
  "#c03a7a",
  "#4a7d1f",
  "#3f6fd1",
];

const VARIANTS = [
  [0, 0],
  [0.1, 0],
  [0, 0.45],
  [0, -0.45],
  [-0.1, 0],
  [0.2, 0],
  [0.1, 0.45],
  [0.1, -0.45],
  [-0.2, 0],
  [0.3, 0],
  [0.2, 0.45],
  [0.2, -0.45],
] as const;

function separate(
  colors: Map<string, string>,
  votes: Array<readonly [string | null, number]>,
  branch: Map<string, string> = new Map(),
  isHued = false,
) {
  const weight = new Map<string, number>();
  for (const [party, count] of votes) {
    if (party != null) {
      weight.set(party, (weight.get(party) ?? 0) + count);
    }
  }
  const order = weight
    .keys()
    .toArray()
    .toSorted((a, b) => (weight.get(b) ?? 0) - (weight.get(a) ?? 0));
  const placed: Array<Array<number>> = [];
  for (const party of order) {
    const hex = colors.get(party) ?? "";
    if (!/^#[\da-f]{6}$/iu.test(hex)) {
      continue;
    }
    const [r = 0, g = 0, b = 0] = [1, 3, 5].map((index) => {
      const value = Number.parseInt(hex.slice(index, index + 2), 16) / 255;
      return value <= 0.04045
        ? value / 12.92
        : ((value + 0.055) / 1.055) ** 2.4;
    });
    const [l, m, s] = [
      0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
      0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
      0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
    ].map((value) => Math.cbrt(value)) as [number, number, number];
    const shown = Math.min(
      0.86,
      Math.max(0.52, 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s),
    );
    const green = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
    const blue = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
    const hue = Math.atan2(blue, green);
    const candidates = (
      isHued
        ? VARIANTS.toSorted(
            (a, c) =>
              Number(a[1] === 0 && a[0] !== 0) -
              Number(c[1] === 0 && c[0] !== 0),
          )
        : VARIANTS
    )
      .filter(([step]) => shown + step >= 0.52 && shown + step <= 0.86)
      .map(([step, turn]) => {
        const lightness = shown + step;
        let chroma = Math.hypot(green, blue);
        let fitted = { at: [lightness, green, blue], rgb: [0, 0, 0] };
        for (let attempt = 0; attempt < 24; attempt++) {
          const at = [
            lightness,
            chroma * Math.cos(hue + turn),
            chroma * Math.sin(hue + turn),
          ];
          const [cl = 0, cm = 0, cs = 0] = [
            0.3963377774 * (at[1] ?? 0) + 0.2158037573 * (at[2] ?? 0),
            -0.1055613458 * (at[1] ?? 0) - 0.0638541728 * (at[2] ?? 0),
            -0.0894841775 * (at[1] ?? 0) - 1.291485548 * (at[2] ?? 0),
          ].map((value) => (lightness + value) ** 3);
          fitted = {
            at,
            rgb: [
              4.0767416621 * cl - 3.3077115913 * cm + 0.2309699292 * cs,
              -1.2684380046 * cl + 2.6097574011 * cm - 0.3413193965 * cs,
              -0.0041960863 * cl - 0.7034186147 * cm + 1.707614701 * cs,
            ],
          };
          if (
            fitted.rgb.every((value) => value >= -0.0005 && value <= 1.0005)
          ) {
            break;
          }
          chroma *= 0.9;
        }
        return {
          ...fitted,
          kept: step === 0 && turn === 0,
          distance: Math.min(
            ...placed.map((other) =>
              Math.hypot(
                ...fitted.at.map(
                  (value, index) =>
                    (value - (other[index] ?? 0)) *
                    (isHued && index === 0 ? 0.5 : 1),
                ),
              ),
            ),
          ),
        };
      });
    const best =
      candidates.find((candidate) => candidate.distance >= 0.1) ??
      candidates.toSorted((a, c) => c.distance - a.distance).at(0);
    if (best == null) {
      continue;
    }
    placed.push(best.at);
    if (!best.kept) {
      colors.set(
        party,
        `#${best.rgb
          .map((value) => {
            const clipped = Math.min(1, Math.max(0, value));
            return Math.round(
              255 *
                (clipped <= 0.0031308
                  ? 12.92 * clipped
                  : 1.055 * clipped ** (1 / 2.4) - 0.055),
            )
              .toString(16)
              .padStart(2, "0");
          })
          .join("")}`,
      );
    }
  }
  for (const [party, parent] of branch) {
    colors.set(party, colors.get(parent) ?? "");
  }
}

async function load(
  roots: Array<string>,
  areaKs: Array<number>,
  date: string,
  outcomes: Array<string>,
  owners: Array<string> = [],
  step: number | null = null,
  facts: keyof typeof FACTS = "area",
  spread: Map<number, Array<[number, number]>> | null = null,
  isFirst = false,
  only: Array<number> | null = null,
) {
  const loaded = new Map<
    number,
    {
      side: Side;
      measure: string;
      isTally: boolean;
      appointed: number | null;
      complete: boolean;
      ks: Array<number>;
      sets: Array<v.InferOutput<typeof ResultSet>>;
    }
  >();
  if (areaKs.length === 0) {
    return loaded;
  }
  const units = sql`${JSON.stringify(
    facts === "pool"
      ? await remember("unlocated", async () =>
          Object.fromEntries(
            (
              await db.execute<{ polity: string; units: Array<number> }>(sql`
                with recursive up(k, id) as (
                  select u.k, u.polity_id from reporting_unit u where u.area_id is null
                  union
                  select up.k, p.parent_id from polity_parent p join up on p.polity_id = up.id
                )
                select id::text as polity, array_agg(k order by k) as units from up group by id
              `)
            ).rows.map((row) => [row.polity, row.units]),
          ),
        )
      : {},
  )}::jsonb`;
  const picked = await db.execute<{
    area: number;
    k: number;
    contest: string;
    root: string;
    stage: string;
    status: string;
    completeness: number | null;
    is_default: boolean;
    single: boolean;
    kind: string;
    publisher: string;
    title: string;
    url: string | null;
    license: string | null;
    attribution: string | null;
    unreconciled: v.InferOutput<typeof Unreconciled> | null;
  }>(sql`
    with recursive grp(id, root) as (
      select id, id from unnest(${sql.param(roots)}::uuid[]) as r(id)
      union all
      select c.id, grp.root from contest c join grp on c.allocation_parent_id = grp.id
    ),
    sets as materialized (
      select rs.k, rs.id, rs.contest_id, grp.root, rs.stage, rs.status, rs.completeness, rs.is_default, rs.source_id
      from grp cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs
      ${only == null ? sql`` : sql`where rs.k = any(${sql.param(only)}::int[])`}
    ),
    picked as (
      select distinct on (rs.contest_id, a.k) a.k as area, rs.k, rs.id, rs.contest_id as contest, rs.root, rs.stage, rs.status,
        rs.completeness, rs.is_default, rs.source_id
      from sets rs
      ${
        owners.length === 0
          ? sql`cross join unnest(${sql.param(areaKs)}::int[]) as a(k)`
          : sql`join unnest(${sql.param(areaKs)}::int[], ${sql.param(owners)}::uuid[]) as a(k, owner) on a.owner = rs.root`
      }
      join lateral (
        select count(distinct f.option_k) filter (where f.option_k <> 0) as options
        from (${FACTS[facts](sql`rs.k`, sql`a.k`, units)}) f where f.measure = any(${COUNTED}) and f.value > 0
        having count(*) > 0
      ) hit on true
      order by rs.contest_id, a.k,
        exists (select from source s where s.id = rs.source_id and s.license ilike '%ICPSR%'),
        (exists (select from source s where s.id = rs.source_id and s.kind = 'official_body')
          and exists (select from result_rollup_unit u where u.result_set_k = rs.k and u.area_k = a.k)) desc,
        rs.is_default desc, array_position(${STAGES}, rs.stage), hit.options desc, rs.completeness desc nulls last
    )
    select p.area, p.k, p.contest, p.root, p.stage::text, p.status::text, p.completeness::float8 as completeness, p.is_default,
      coalesce((select c.magnitude from contest c where c.id = p.contest), 1) <= 1 as single,
      s.kind::text, s.publisher, s.title, s.url, s.license, s.attribution,
      (select f.details from flag f where f.result_set_id = p.id and f.check_code = 'reconcile.official' limit 1) as unreconciled
    from picked p join source s on s.id = p.source_id
  `);
  if (picked.rows.length === 0) {
    return loaded;
  }
  const rollup = await db.execute<{
    set: number;
    area: number;
    option: number;
    role: string;
    main: number;
    value: number;
    complete: boolean;
    rank: number;
    channel: number;
  }>(sql`
    with fetched as materialized (
      select r.result_set_k as set, r.area_k as area, r.option_k as option, r.measure, r.count_round,
        r.value::float8 as value, r.complete, r.channel,
        coalesce(${step}::int, case when p.single and ${!isFirst}
          then max(r.count_round) over (partition by r.result_set_k, r.area_k, r.measure)
          else min(r.count_round) over (partition by r.result_set_k, r.area_k, r.measure) end) as first,
        min(r.count_round) over (partition by r.result_set_k, r.area_k, r.measure) as opening,
        p.single and ${!isFirst}
          and max(r.count_round) filter (where r.measure = any(${COUNTED})) over (partition by r.result_set_k, r.area_k)
            > min(r.count_round) filter (where r.measure = any(${COUNTED})) over (partition by r.result_set_k, r.area_k) as late
      from unnest(${sql.param(picked.rows.map((row) => row.k))}::int[], ${sql.param(picked.rows.map((row) => row.area))}::int[], ${sql.param(picked.rows.map((row) => row.single))}::boolean[]) as p(set, area, single)
      join lateral (${FACTS[facts](sql`p.set`, sql`p.area`, units)}) r on true
      where (r.measure = any(${COUNTED}) or r.option_k = 0 and (r.measure = 2 or r.measure = 4 and ${step}::int is null))
    ),
    mains as materialized (
      select area, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main from fetched group by area
    ),
    gone as materialized (
      select r.result_set_k as set, r.option_k as option
      from unnest(${sql.param([...new Set(picked.rows.map((row) => row.k))])}::int[]) as s(k) cross join lateral (
        select x.result_set_k, x.option_k, x.value from result_rollup x
        where ${step}::int is not null and x.result_set_k = s.k and x.measure = any(${COUNTED}) and x.count_round = ${step}::int
        offset 0
      ) r
      group by 1, 2 having max(r.value) = 0
    ),
    flagged as (
      select f.*, m.main, bool_or(f.measure = m.main) over (partition by f.set, f.area) as kept,
        sum(f.value) filter (where f.count_round = f.opening and f.measure = m.main) over (partition by f.option) as rank
      from fetched f join mains m on m.area = f.area
    ),
    whole as (
      select distinct on (x.option_k) x.option_k as option, x.value::float8 as total
      from (select distinct set from fetched) s
      join result_set rs on rs.k = s.set
      join contest c on c.id = rs.contest_id
      join area a on a.id = c.district_area_id
      cross join lateral (
        select y.option_k, y.measure, y.count_round, y.value from result_rollup y
        where y.result_set_k = s.set and y.area_k = a.k and y.measure = any(${COUNTED}) and y.option_k <> 0
        offset 0
      ) x
      order by x.option_k, array_position(${COUNTED}, x.measure), x.count_round
    )
    select r.set, r.area, r.option, x.role, r.main, r.value, r.complete, 0::float8 as rank, r.channel
    from flagged r
    cross join lateral (
      select case
        when r.measure = r.main then 'votes'
        when r.measure = 4 and r.main = 1 then 'valid'
        when r.measure = 2 and r.main = 11 then 'appointed'
      end as role
    ) x
    where r.kept and r.count_round = r.first and x.role is not null and not (r.late and x.role = 'valid')
      and not exists (select from gone g where g.set = r.set and g.option = r.option)
    union all
    select distinct on (r.option) r.set, r.area, r.option, 'rank', r.main, 0, true, coalesce(w.total, r.rank)::float8, 0
    from flagged r left join whole w on w.option = r.option where r.rank is not null or w.total is not null
  `);
  const counts = rollup.rows
    .filter((row) => row.role !== "rank")
    .flatMap((row) =>
      (spread?.get(row.area) ?? [[row.area, 1] as const]).map(
        ([area, weight]) => ({ ...row, area, value: row.value * weight }),
      ),
    );
  const kept = new Set(counts.map((row) => `${row.set}:${row.area}`));
  picked.rows = new Map(
    picked.rows.flatMap((row) =>
      (spread?.get(row.area) ?? [[row.area, 1] as const]).map(
        ([area]) => [`${row.k}:${area}`, { ...row, area }] as const,
      ),
    ),
  )
    .values()
    .toArray();
  const tallies = new Map(
    rollup.rows
      .filter((row) => row.role === "rank")
      .map((row) => [row.option, row.rank]),
  );
  const areas = new Map<
    number,
    {
      votes: Map<number, number>;
      sums: Map<string, number>;
      main: number;
      complete: boolean;
    }
  >();
  for (const row of counts) {
    const area = areas.get(row.area) ?? {
      votes: new Map<number, number>(),
      sums: new Map<string, number>(),
      main: row.main,
      complete: true,
    };
    areas.set(row.area, area);
    area.complete &&= row.complete;
    const sum = `${row.role}:${row.set}:${row.channel}`;
    area.sums.set(sum, (area.sums.get(sum) ?? 0) + row.value);
    if (row.role === "votes") {
      area.votes.set(row.option, (area.votes.get(row.option) ?? 0) + row.value);
    }
  }
  const delegated = new Set(
    counts
      .filter((row) => row.main === 12 || row.main === 27)
      .map((row) => row.set),
  );
  const aligned = new Set(
    delegated.size === 0
      ? []
      : (
          await db.execute<{ set: number }>(sql`
            select s.k as set from unnest(${sql.param([...delegated])}::int[]) as s(k)
            where exists (select from result_value v where v.result_set_k = s.k and v.measure in (17, 18))
          `)
        ).rows.map((row) => row.set),
  );
  const optionKs = new Set(tallies.keys());

  const options = await db.execute<{
    k: number;
    contest: string;
    kind: string;
    party: string | null;
    person: string | null;
    label: string | null;
    candidates: number | null;
    outcome: string | null;
    note: string | null;
    declined: boolean;
    seats: number | null;
    alliance: string | null;
    is_line: boolean;
    independent: boolean;
    incumbent: boolean;
  }>(sql`
    with covered as (${covered(sql`
      select contest_id from contest_option
      where k = any(${sql.param([...optionKs])}::int[]) or contest_id = any(${sql.param(outcomes)}::uuid[])
    `)})
    select o.k, o.contest_id as contest, o.kind::text, coalesce(pm.into_id, o.party_id, (
        select coalesce(mm.into_id, cy.member_of_party_id, ta.party_id, nm.party_id, nb.party)
        from candidacy cy
        left join lateral (
          select n.party_id from candidacy_nomination n where n.candidacy_id = cy.id and n.kind = 'nominated'
          order by (
            select max(x.value) from contest_option ch
            join result_set rs on rs.contest_id = ch.contest_id and ${latest("rs")}
            cross join lateral (select x.value from result_rollup x where x.result_set_k = rs.k and x.option_k = ch.k offset 0) x
            where ch.parent_option_id = o.id and ch.party_id = n.party_id
          ) desc nulls last, n.party_id
          limit 1
        ) nm on true
        left join lateral (
          select a.party_id from tenure t join tenure_affiliation a on a.tenure_id = t.id and a.kind = 'party' and a.party_id is not null
          where t.person_id = cy.person_id and t.contest_id = o.contest_id order by lower(a.valid) nulls first limit 1
        ) ta on true
        left join lateral (
          select coalesce(xm.into_id, xo.party_id) as party
          from candidacy xc
          join contest_option xo on xo.id = xc.option_id and xo.party_id is not null
          join contest xk on xk.id = xo.contest_id and xk.party_scope_id is null
          join election xe on xe.id = xk.election_id
          left join entity_merge xm on xm.entity_id = xo.party_id
          where xc.person_id = cy.person_id and xc.id <> cy.id and not xc.is_independent
            and (o.alliance_id is null or exists (
              select from alliance_member am where am.alliance_id = o.alliance_id and am.party_id in (xo.party_id, xm.into_id)
            ))
            and xe.date_from between (select e.date_from - 2200 from contest k join election e on e.id = k.election_id where k.id = o.contest_id)
              and (select e.date_from + 2200 from contest k join election e on e.id = k.election_id where k.id = o.contest_id)
          order by abs(xe.date_from - (select e.date_from from contest k join election e on e.id = k.election_id where k.id = o.contest_id)), xe.date_from desc
          limit 1
        ) nb on cy.member_of_party_id is null and nm.party_id is null and ta.party_id is null
        left join entity_merge mm on mm.entity_id = coalesce(cy.member_of_party_id, ta.party_id, nm.party_id)
        where o.parent_option_id is null and o.kind in ('candidate', 'ticket') and cy.option_id = o.id and cy.role = 'head' and not cy.is_independent
          and not exists (select from candidacy cz where cz.option_id = o.id and cz.role = 'head' and cz.id <> cy.id)
        limit 1
      )) as party, o.ballot_label as label, o.candidate_count as candidates,
      o.alliance_id as alliance, o.parent_option_id is not null as is_line,
      exists (select from candidacy cy where cy.option_id = o.id and cy.role = 'head' and cy.is_independent) as independent,
      case when o.contest_id in (select id from covered) then exists (
          select from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
          where cy.option_id = o.id and cy.role = 'head' and ${tenured(sql`coalesce(m.into_id, cy.person_id)`, sql`o.contest_id`)}
            and (o.kind <> 'write_in' or exists (
              select from tenure wt
              join contest wk on wk.id = o.contest_id
              join election we on we.id = wk.election_id
              left join seat ws on ws.id = wt.seat_id
              left join contest wc on wc.id = wt.contest_id
              where wt.person_id in (cy.person_id, m.into_id) and ${holding("wt", sql`we.date_from`)}
                and coalesce(ws.district_area_id, wc.district_area_id) = wk.district_area_id
            ))
        )
        else exists (select from candidacy cy where cy.option_id = o.id and cy.role = 'head' and cy.is_incumbent)
      end as incumbent,
      (select coalesce(m.into_id, cy.person_id) from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
       where cy.option_id in (o.id, o.parent_option_id) and cy.role = 'head'
         and not exists (select from candidacy cz where cz.option_id = cy.option_id and cz.role = 'head' and cz.id <> cy.id)
       order by cy.option_id = o.id desc, cy.id limit 1) as person,
      oc.kind::text as outcome, oc.note, coalesce(oc.declined, false) as declined,
      case when o.kind = 'list' and ${pooled(sql`o.id`)} then null
        when o.parent_option_id is null or ${pooled(sql`o.parent_option_id`)} or not exists (
          select from outcome y join result_set ry on ry.id = y.result_set_id and ${latest("ry")}
          where y.option_id = o.parent_option_id and y.kind in ('elected', 'won') and y.candidacy_id is null
          offset 0
        ) then oc.seats end as seats
    from contest_option o
    left join entity_merge pm on pm.entity_id = o.party_id
    left join lateral (
      select (array_agg(x.kind order by array_position(array['elected', 'advanced', 'won', 'tied', 'withdrew', 'disqualified']::outcome_kind[], x.kind)) filter (where x.kind <> 'declined'))[1] as kind,
        (array_agg(x.notes order by array_position(array['elected', 'advanced', 'won', 'tied', 'withdrew', 'disqualified', 'declined']::outcome_kind[], x.kind)) filter (where x.notes is not null))[1] as note,
        bool_or(x.kind = 'declined') as declined,
        coalesce(max(x.seats), nullif(count(*) filter (where x.kind = 'elected'), 0))::int as seats
      from outcome x join result_set rs on rs.id = x.result_set_id
      where x.option_id = o.id and o.contest_id = any(${sql.param(outcomes)}::uuid[]) and ${latest("rs")}
        and (x.kind in ('elected', 'won', 'advanced', 'tied', 'withdrew', 'disqualified', 'declined') or x.kind = 'lost' and o.kind = 'answer') and x.candidacy_id is null
        and not (x.kind in ('withdrew', 'disqualified') and exists (
          select from contest_option lp where lp.id = o.parent_option_id and lp.kind = 'list'
        ))
    ) oc on true
    where (o.k = any(${sql.param([...optionKs])}::int[]) or o.contest_id = any(${sql.param(outcomes)}::uuid[]))
      and (o.k = any(${sql.param([...optionKs])}::int[]) or oc.kind is not null)
      and not (o.kind = 'list' and ${pooled(sql`o.id`)})
  `);
  const [backing, coalitions] = await Promise.all([
    db.execute<{ party: string; person: string }>(sql`
      with recursive grp(id) as (
        select unnest(${sql.param(roots)}::uuid[])
        union all
        select c.id from contest c join grp on c.allocation_parent_id = grp.id
      )
      select distinct coalesce(pm.into_id, n.party_id) as party, coalesce(m.into_id, cy.person_id) as person
      from candidacy_nomination n
      join candidacy cy on cy.id = n.candidacy_id
      join contest_option o on o.id = cy.option_id
      join grp on grp.id = o.contest_id
      left join entity_merge pm on pm.entity_id = n.party_id
      left join entity_merge m on m.entity_id = cy.person_id
      where n.kind in ('endorsed', 'supported')
    `),
    options.rows.every((row) => row.alliance == null)
      ? { rows: [] }
      : db.execute<{ alliance: string; party: string; wins: number }>(sql`
      select distinct on (m.alliance_id) m.alliance_id as alliance, coalesce(pm.into_id, m.party_id) as party, w.wins
      from alliance_member m
      join alliance al on al.id = m.alliance_id
      left join entity_merge pm on pm.entity_id = m.party_id
      cross join lateral (
        select count(*)::int as wins
        from election e
        join contest c on c.election_id = e.id
        join contest_option o on o.contest_id = c.id and o.party_id = m.party_id and o.alliance_id = m.alliance_id
        join outcome x on x.option_id = o.id and x.kind in ('elected', 'won')
        where e.id = al.election_id or e.parent_id = al.election_id
      ) w
      where m.alliance_id = any(${sql.param([...new Set(options.rows.flatMap((row) => row.alliance ?? []))])}::uuid[])
      order by m.alliance_id, w.wins desc
    `),
  ]);
  const anchor = new Map(
    coalitions.rows.map((row) => [row.alliance, row.party]),
  );
  const leading = new Map(
    coalitions.rows.flatMap((row) =>
      row.wins > 0 ? [[row.alliance, row.party] as const] : [],
    ),
  );
  const stand = (row: {
    party: string | null;
    person: string | null;
    alliance: string | null;
  }) =>
    row.party ??
    (row.person == null ? null : anchor.get(row.alliance ?? "")) ??
    null;
  const entities = [
    ...new Set([
      ...options.rows.flatMap((row) =>
        [row.party, row.person, row.alliance].filter((id) => id != null),
      ),
      ...backing.rows.map((row) => row.person),
    ]),
  ];
  const parties = [
    ...new Set(
      options.rows.map((row) => stand(row)).filter((id) => id != null),
    ),
  ];
  const [labels, colors] = await Promise.all([
    names(entities, date),
    parties.length === 0
      ? { rows: [] }
      : db.execute<{
      party_id: string;
      color: string | null;
      parent: string | null;
      lean: number | null;
    }>(sql`
      select p.id as party_id, c.color, b.parent, (
        select (low + high) * 100 + coalesce(rank, 99) from party_position
        where party_id = p.id and valid @> ${date}::date limit 1
      ) as lean
      from unnest(${sql.param(parties)}::uuid[]) p(id)
        left join lateral (
          select coalesce(m.into_id, a.parent_party_id) as parent from party_affiliation a
          left join entity_merge m on m.entity_id = a.parent_party_id
          where a.party_id = p.id and a.kind = 'regional_branch' and a.valid @> ${date}::date
          limit 1
        ) b on true,
        lateral (select coalesce(
          ${paint(sql`b.parent`, sql`select election_id from contest where id = any(${sql.param(roots)}::uuid[])`, sql`${date}::date`)},
          ${paint(sql`p.id`, sql`select election_id from contest where id = any(${sql.param(roots)}::uuid[])`, sql`${date}::date`)}
        ) as color) c
    `),
  ]);
  const branch = new Map(
    colors.rows.flatMap((row) =>
      row.color == null ? [] : [[row.party_id, row.parent ?? row.party_id]],
    ),
  );
  const color = new Map(
    colors.rows.flatMap((row) =>
      row.color == null
        ? []
        : [
            [row.party_id, row.color],
            [row.parent ?? row.party_id, row.color],
          ],
    ),
  );
  const lean = new Map(
    colors.rows.flatMap((row) =>
      row.lean == null ? [] : [[row.party_id, row.lean]],
    ),
  );
  const top = Math.max(0, ...tallies.values());
  const rootOf = new Map(picked.rows.map((row) => [row.contest, row.root]));
  const partisan = new Set(
    options.rows.flatMap((row) =>
      stand(row) == null ? [] : [rootOf.get(row.contest) ?? row.contest],
    ),
  );
  for (const group of Map.groupBy(
    options.rows.filter(
      (row) =>
        row.independent &&
        row.person != null &&
        stand(row) == null &&
        partisan.has(rootOf.get(row.contest) ?? row.contest),
    ),
    (row) => rootOf.get(row.contest) ?? row.contest,
  ).values()) {
    const order = new Set(
      group
        .toSorted((a, b) => (tallies.get(b.k) ?? 0) - (tallies.get(a.k) ?? 0))
        .map((row) => row.person ?? ""),
    );
    for (const [rank, person] of [...order].entries()) {
      color.set(person, independent(rank));
    }
  }
  const independents = options.rows
    .filter(
      (row) =>
        !color.has(stand(row) ?? row.person ?? "") &&
        row.person != null &&
        (tallies.get(row.k) ?? 0) >= top * 0.05,
    )
    .map((row) => stand(row) ?? row.person ?? "");
  for (const id of independents) {
    color.set(
      id,
      UNAFFILIATED[Number.parseInt(id.slice(-4), 16) % UNAFFILIATED.length] ??
        "",
    );
  }
  const side = new Map(
    options.rows.map((row) => {
      const id = stand(row);
      return [row.k, id == null ? row.person : (branch.get(id) ?? id)] as const;
    }),
  );
  const tinted = new Map<
    number,
    { party: string; rank: number; own: string | null }
  >();
  for (const [root, rows] of Map.groupBy(
    options.rows.filter((row) => row.person != null && stand(row) != null),
    (row) => rootOf.get(row.contest) ?? row.contest,
  )) {
    const parties = Map.groupBy(rows, (row) => side.get(row.k) ?? "");
    for (const [party, group] of parties) {
      const base = color.get(party);
      const weight = new Map<string, number>();
      for (const row of group) {
        weight.set(
          row.person ?? "",
          (weight.get(row.person ?? "") ?? 0) + (tallies.get(row.k) ?? 0),
        );
      }
      const order = weight
        .keys()
        .toArray()
        .toSorted((a, b) => (weight.get(b) ?? 0) - (weight.get(a) ?? 0));
      for (const row of group) {
        const rank = order.indexOf(row.person ?? "");
        if (base == null || rank < 1) {
          continue;
        }
        const own =
          parties.size === 1 ? null : `${root}:${party}:${row.person ?? ""}`;
        if (own != null) {
          color.set(own, base);
          side.set(row.k, own);
        }
        tinted.set(row.k, { party, rank, own });
      }
    }
  }
  separate(
    color,
    options.rows.map(
      (row) => [side.get(row.k) ?? null, tallies.get(row.k) ?? 0] as const,
    ),
    branch,
  );
  const tints = new Map(
    tinted
      .entries()
      .map(([k, { party, rank, own }]) => [
        k,
        own == null
          ? `oklch(from ${color.get(party) ?? ""} l max(c, 0.1) calc(h + ${rank * 137.5}))`
          : (color.get(own) ?? ""),
      ]),
  );
  const backers = backing.rows.map((row) => ({
    party: row.party,
    candidate: labels.full.get(row.person) ?? "",
  }));
  for (const [k, area] of areas) {
    const sets = picked.rows.filter(
      (row) => row.area === k && kept.has(`${row.k}:${row.area}`),
    );
    const isAnswers = options.rows.every(
      (row) => !area.votes.has(row.k) || row.kind === "answer",
    );
    let total = 0;
    let appointed = 0;
    for (const set of sets) {
      let [valid, votes, missing] = [0, 0, 0];
      for (const [key, value] of area.sums) {
        const [role, k, channel] = key.split(":");
        if (k !== String(set.k)) {
          continue;
        }
        if (role === "appointed") {
          appointed += value;
        } else if (role === "valid") {
          valid += value;
        } else if (role === "votes") {
          votes += value;
          if (!area.sums.has(`valid:${set.k}:${channel}`)) {
            missing += value;
          }
        }
      }
      total +=
        isAnswers || valid === 0
          ? votes
          : valid >= votes
            ? valid
            : valid + missing;
    }
    loaded.set(k, {
      side: {
        valid: total,
        options: options.rows
          .filter(
            (row) =>
              area.votes.has(row.k) ||
              (row.outcome != null && outcomes.includes(row.contest)),
          )
          .map((row): Option => ({
            contest: row.contest,
            kind: row.kind,
            party: row.party,
            person: row.person,
            label:
              labels.full.get(row.person ?? "") ??
              labels.full.get(row.party ?? "") ??
              (row.kind === "independents"
                ? row.candidates == null
                  ? m.option_independents()
                  : m.option_independents_count({ count: row.candidates })
                : undefined) ??
              (row.kind === "others"
                ? (
                    {
                      Übrige: m.option_others,
                      others: m.option_others,
                      ".": m.option_others,
                      Altri: m.option_others,
                      "Voti dispersi": m.option_scattered,
                      Einzelbewerber: m.option_individuals,
                      Bağımsızlar: m.option_independents,
                      "Diğer partiler ve bağımsızlar":
                        m.option_others_independents,
                    } as Record<string, (() => string) | undefined>
                  )[row.label ?? ""]?.()
                : undefined) ??
              row.label ??
              row.kind,
            short:
              labels.short.get(row.party ?? "") ??
              (row.person == null ? null : labels.full.get(row.party ?? "")) ??
              null,
            color:
              tints.get(row.k) ??
              color.get(stand(row) ?? row.person ?? "") ??
              null,
            lean: lean.get(stand(row) ?? "") ?? null,
            votes: area.votes.get(row.k) ?? 0,
            outcome: row.outcome,
            note: row.note,
            isDeclined: row.declined,
            isIndependent: row.independent,
            isIncumbent: row.incumbent,
            seats: row.seats,
            alliance:
              row.alliance == null
                ? null
                : {
                    id: row.alliance,
                    label: labels.full.get(row.alliance) ?? "",
                    anchor: leading.get(row.alliance) ?? null,
                    color: color.get(leading.get(row.alliance) ?? "") ?? null,
                  },
            line: row.is_line
              ? (labels.full.get(row.party ?? "") ??
                /\(([^()]+)\)$/u.exec(row.label ?? "")?.[1] ??
                row.label)
              : null,
          })),
        backing: backers,
      },
      measure: MEASURES.get(area.main) ?? "votes",
      isTally:
        area.main === 11 ||
        ((area.main === 12 || area.main === 27) &&
          !sets.some((set) => aligned.has(set.k))),
      appointed: appointed > 0 ? appointed : null,
      complete: area.complete,
      ks: sets.map((set) => set.k),
      sets: sets.map((row) => ({
        stage: row.stage,
        status: row.status,
        completeness: row.completeness,
        isDefault: row.is_default,
        unreconciled: row.unreconciled,
        source: {
          kind: row.kind,
          publisher: row.publisher,
          title: row.title,
          url: row.url,
          license: row.license,
          attribution: row.attribution,
        },
      })),
    });
  }
  return loaded;
}

const won = (set: SQL, option: SQL) => sql`
  coalesce((
    select sum(coalesce(x.seats, 1)) from outcome x
    where x.result_set_id = ${set} and x.option_id = ${option}
      and x.kind in ('elected', 'won') and x.candidacy_id is null
  ), 0)::int
`;

function holdings(
  seats: Iterable<[string, number]>,
  position: Map<string, number>,
): Array<[number, number]> | null {
  const bands = new Map<number, number>();
  for (const [key, count] of seats) {
    const at = position.get(key);
    if (at != null && count > 0) {
      bands.set(at, (bands.get(at) ?? 0) + count);
    }
  }
  return bands.size > 1
    ? bands
        .entries()
        .toArray()
        .toSorted((a, b) => b[1] - a[1])
    : null;
}

type Split = Map<
  number,
  Array<{ name: string; path: string | null; seats: number }>
>;

type Holder = {
  contest: string;
  area: string | null;
  path: string | null;
  k: number;
  kind: string;
  party: string | null;
  isIndependent: boolean;
  seats: number;
  people: Array<{
    person: string;
    position: number | null;
    isSeated: boolean;
    isElected: boolean;
  }>;
};

async function units(
  contest: string,
  area: { id: string; k: number },
  date: string,
  base: string | null,
) {
  const kids = await children(contest, area, true, date);
  const found = await db.execute<{
    area: number;
    option: number;
    seats: number;
  }>(sql`
    select r.area_k as area, r.option_k as option, sum(r.value)::int as seats
    from result_set rs cross join lateral (
      select r.area_k, r.option_k, r.value from result_rollup r
      where r.result_set_k = rs.k and r.measure = 16 and r.count_round = 0
        and r.area_k = any(${sql.param(kids.map((kid) => kid.k))}::int[]) offset 0
    ) r
    where rs.contest_id = ${contest}::uuid and ${latest("rs")}
    group by r.area_k, r.option_k
    having sum(r.value) > 0
  `);
  const kid = new Map(kids.map((entry) => [entry.k, entry]));
  const totals = new Map<string, number>();
  for (const row of found.rows) {
    const scheme = kid.get(row.area)?.scheme ?? "";
    totals.set(scheme, (totals.get(scheme) ?? 0) + row.seats);
  }
  const scheme = [...totals].toSorted((a, b) => b[1] - a[1])[0]?.[0];
  const split: Split = new Map();
  for (const row of found.rows) {
    const entry = kid.get(row.area);
    if (entry == null || entry.scheme !== scheme) {
      continue;
    }
    const list = split.get(row.option) ?? [];
    list.push({
      name: entry.name,
      path: base == null ? null : `${base}/${entry.segment}`,
      seats: row.seats,
    });
    split.set(row.option, list);
  }
  return split;
}

function slots(
  option: Holder,
  labels: Awaited<ReturnType<typeof names>>,
  split: Split | null,
) {
  const isSeated = option.people.some((entry) => entry.isSeated);
  const isElected = option.people.some((entry) => entry.isElected);
  const chosen = option.people
    .filter((entry) =>
      isSeated
        ? entry.isSeated
        : isElected
          ? entry.isElected
          : option.people.length <= option.seats,
    )
    .toSorted((a, b) => (a.position ?? Infinity) - (b.position ?? Infinity));
  const own = {
    id: option.contest,
    name:
      option.area == null
        ? (labels.full.get(option.contest) ?? "").replace(/^.+? in /u, "")
        : (labels.full.get(option.area) ?? ""),
    path: option.path,
  };
  const party = option.party;
  const affiliation = party == null ? null : (labels.full.get(party) ?? null);
  const result: Array<{
    place: { id: string; name: string; path: string | null };
    name: string | null;
    position: number | null;
    person: string | null;
    party: string | null;
    affiliation: string | null;
  }> = chosen.map((entry) => ({
    place: own,
    name: labels.full.get(entry.person) ?? null,
    position: entry.position,
    person: entry.person,
    party,
    affiliation,
  }));
  const units = chosen.length === 0 ? (split?.get(option.k) ?? []) : [];
  for (const unit of units) {
    const place = {
      id: unit.path ?? unit.name,
      name: unit.name,
      path: unit.path,
    };
    const end = Math.min(option.seats, result.length + unit.seats);
    while (result.length < end) {
      result.push({
        place,
        name: null,
        position: null,
        person: null,
        party,
        affiliation,
      });
    }
  }
  while (result.length < option.seats) {
    result.push({
      place: own,
      name: null,
      position: null,
      person: null,
      party,
      affiliation,
    });
  }
  return result;
}

function assign(
  options: Array<Holder>,
  labels: Awaited<ReturnType<typeof names>>,
  lineage: Array<Relation>,
  keys: Set<string>,
  split: Split | null,
) {
  const root = ancestry(lineage, "party");
  const seats = new Map<string, ReturnType<typeof slots>>();
  for (const option of options) {
    const loose = keys
      .values()
      .filter((key) => key.startsWith(`kind:${option.kind}`))
      .toArray();
    const key = [
      option.party == null ? null : `party:${root(option.party)}`,
      option.people.length === 1 ? `person:${option.people[0]?.person}` : null,
      option.party == null &&
      (option.isIndependent || option.kind === "independents")
        ? "independent"
        : null,
      option.party == null && loose.length === 1 ? loose[0] : null,
    ].find((entry) => entry != null && keys.has(entry));
    if (key != null) {
      seats.set(key, [
        ...(seats.get(key) ?? []),
        ...slots(option, labels, split),
      ]);
    }
  }
  const places = new Map(
    seats
      .values()
      .flatMap((list) =>
        list.map((slot) => [slot.place.id, slot.place] as const),
      ),
  );
  const at = new Map(places.keys().map((id, index) => [id, index]));
  return {
    places: places.values().toArray(),
    seats: new Map(
      seats
        .entries()
        .map(([key, list]) => [
          key,
          list
            .toSorted(
              (a, b) =>
                a.place.name.localeCompare(b.place.name, undefined, {
                  numeric: true,
                }) || (at.get(a.place.id) ?? 0) - (at.get(b.place.id) ?? 0),
            )
            .map((slot) => ({
              place: at.get(slot.place.id) ?? 0,
              name: slot.name,
              position: slot.position,
              person: slot.person,
              party: slot.party,
              affiliation: slot.affiliation,
            })),
        ]),
    ),
  };
}

async function holders(
  ids: Array<string>,
  date: string | null,
  lineage: Array<Relation>,
  keys: Set<string>,
  split: Split | null,
) {
  const leaves = await db.execute<{ id: string }>(sql`
    with recursive tree(id) as (
      select unnest(${sql.param(ids)}::uuid[])
      union all
      select c.id from contest c join tree on c.allocation_parent_id = tree.id
    )
    select id from tree where not exists (select from contest k where k.allocation_parent_id = tree.id)
  `);
  const found = await db.execute<{
    contest: string;
    area: string | null;
    path: string | null;
    k: number;
    kind: string;
    party: string | null;
    isIndependent: boolean;
    seats: number;
    person: string | null;
    position: number | null;
    isSeated: boolean;
    isElected: boolean;
  }>(sql`
    select c.id as contest, c.district_area_id as area, s.path, o.k, o.kind::text as kind,
      coalesce(pm.into_id, lp.party) as party, coalesce(x.seats, 1) as seats,
      exists (
        select from candidacy cy where cy.option_id = o.id and cy.role = 'head' and cy.is_independent
      ) as "isIndependent",
      coalesce(m.into_id, y.person_id) as person, y.list_position as position,
      coalesce(y.seated, false) as "isSeated", coalesce(y.elected, false) as "isElected"
    from contest c
    left join slug s on s.entity_id = c.id and s.is_current
    join result_set rs on rs.contest_id = c.id and ${latest("rs")}
    join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and (x.seats > 0 or (x.seats is null and x.kind in ('elected', 'won')))
    join contest_option o on o.id = x.option_id
    cross join lateral (select coalesce(o.party_id, ${fused(sql`o.id`, sql`o.contest_id`)}) as party) lp
    left join entity_merge pm on pm.entity_id = lp.party
    left join lateral (
      select cy.person_id, cy.list_position,
        exists (
          select from tenure t
          where t.person_id = cy.person_id and t.contest_id = c.id and t.acquired_via = 'elected'
        ) as seated,
        exists (
          select from outcome z
          where z.candidacy_id = cy.id and z.result_set_id = rs.id and z.kind = 'elected'
        ) as elected
      from candidacy cy where cy.option_id = o.id and cy.role in ('head', 'list_member')
    ) y on true
    left join entity_merge m on m.entity_id = y.person_id
    where c.id = any(${sql.param(leaves.rows.map((row) => row.id))}::uuid[])
  `);
  const options = new Map<string, Holder>();
  for (const row of found.rows) {
    const id = `${row.contest}:${row.k}`;
    const option = options.get(id) ?? { ...row, people: [] };
    option.seats = Math.max(option.seats, row.seats);
    if (
      row.person != null &&
      option.people.every((entry) => entry.person !== row.person)
    ) {
      option.people.push({
        person: row.person,
        position: row.position,
        isSeated: row.isSeated,
        isElected: row.isElected,
      });
    }
    options.set(id, option);
  }
  const labels = await names(
    [
      ...new Set(
        options
          .values()
          .flatMap((option) => [
            option.area ?? option.contest,
            ...(option.party == null ? [] : [option.party]),
            ...option.people.map((entry) => entry.person),
          ]),
      ),
    ],
    date,
  );
  return assign(options.values().toArray(), labels, lineage, keys, split);
}

async function chamber(
  contest: Found,
  area: { id: string; k: number },
  now: ReturnType<typeof load>,
  lineage: Array<Relation>,
  isRoot: boolean,
) {
  const found = await db.execute<{
    size: number | null;
    isParent: boolean;
    need: number | null;
    roots: Array<string>;
    nation: number | null;
    area: string | null;
    polity: string | null;
    path: string | null;
    base: string | null;
  }>(sql`
    select z.seats as size, exists (select from contest k where k.allocation_parent_id = c.id) as "isParent",
      case when c.id = r.id then z.seats end as need,
      case
        when exists (select from contest k where k.allocation_parent_id = c.id) then '{}'
        when c.id <> r.id then array[r.id]
        else (
          select case when count(*) > 1 then array_agg(s.id) else '{}' end from contest s
          where s.election_id = c.election_id and s.body_id = c.body_id and s.allocation_parent_id is null and not s.notional
            and s.party_scope_id is not distinct from c.party_scope_id and s.kind in ('body_seats', 'list_allocation')
        )
      end as roots,
      a.k as nation, a.id as area, case when a.id = pt.area_id then e.polity_id end as polity,
      (select path from slug where entity_id = case when c.id = r.id then c.election_id else r.id end and is_current) as path,
      (select path from slug where entity_id = c.id and is_current) as base
    from contest c
    join contest r on r.id = ${contest.root}::uuid
    join election e on e.id = c.election_id
    left join lateral (
      select z.seats from body_size z
      where z.body_id = c.body_id and (z.valid @> e.date_from or z.valid_from between e.date_from and e.date_from + 150)
      order by z.valid_from desc nulls last limit 1
    ) z on true
    left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
    left join area a on a.id = case when c.id = r.id then pt.area_id else coalesce(r.district_area_id, pt.area_id) end
    where c.id = ${contest.id}::uuid and ${isRoot}::boolean and c.kind in ('body_seats', 'list_allocation')
  `);
  const shape = found.rows[0];
  const whole =
    shape == null
      ? ""
      : createHash("sha1")
          .update(
            `${shape.nation}:${contest.date}:${shape.roots.toSorted().join(",")}`,
          )
          .digest("hex");
  const [loaded, named, current] = await Promise.all([
    shape?.nation == null || shape.roots.length === 0
      ? null
      : remember(`chamber:${whole}`, () =>
          load(
            shape.roots,
            [shape.nation ?? 0],
            contest.date ?? "infinity",
            shape.roots,
          ),
        ),
    names(
      [shape?.area, shape?.polity].filter((id) => id != null),
      contest.date,
    ),
    now,
  ]);
  const seated = (entry: { side: Side } | undefined) =>
    entry == null
      ? []
      : compare(entry.side, null, lineage, "party", "party").filter(
          (row) => (row.current?.seats ?? 0) > 0,
        );
  const own = seated(shape == null ? undefined : current.get(area.k));
  const wide = seated(loaded?.get(shape?.nation ?? 0));
  let [owned, held] = [0, 0];
  for (const row of own) {
    owned += row.current?.seats ?? 0;
  }
  for (const row of wide) {
    held += row.current?.seats ?? 0;
  }
  const isFocus = owned > 0 && held > 0 && held === (shape?.need ?? held);
  const isComplete =
    own.length > 1 &&
    owned === (shape?.isParent === true ? owned : shape?.size);
  if (!isFocus && !isComplete) {
    return null;
  }
  const here = new Map(own.map((row) => [row.key, row]));
  const rows = isFocus ? wide : own;
  const isAlone = shape == null || shape.roots.length === 0;
  const isSingle = isFocus && own.length === 1 && owned === 1;
  const [parties, holding] = await Promise.all([
    names(
      rows.flatMap((row) =>
        row.key.startsWith("party:")
          ? [row.current?.party ?? row.key.slice("party:".length)]
          : [],
      ),
      contest.date,
    ),
    isSingle
      ? null
      : isAlone
        ? (async () =>
            holders(
              [contest.id],
              contest.date,
              lineage,
              new Set(rows.map((row) => row.key)),
              shape?.isParent === true
                ? null
                : await units(
                    contest.id,
                    area,
                    contest.date ?? "infinity",
                    shape?.base ?? null,
                  ),
            ))()
        : remember(
            `holders:${whole}:${JSON.stringify(lineage)}:${rows.map((row) => row.key).join(",")}`,
            () =>
              holders(
                shape.roots,
                contest.date,
                lineage,
                new Set(rows.map((row) => row.key)),
                null,
              ),
          ),
  ]);
  const places = holding?.places ?? [];
  const at = places.findIndex((place) => place.id === contest.id);
  return {
    link: isFocus
      ? {
          name:
            named.full.get(shape?.polity ?? "") ??
            named.full.get(shape?.area ?? "") ??
            "",
          path: shape?.path ?? null,
        }
      : null,
    size: shape?.size ?? null,
    places: places.map((place) => ({ name: place.name, path: place.path })),
    here: at === -1 ? null : at,
    parties: rows.map((row) => ({
      key: row.key,
      label:
        parties.full.get(
          row.current?.party ?? row.key.slice("party:".length),
        ) ?? row.label,
      short:
        parties.short.get(
          row.current?.party ?? row.key.slice("party:".length),
        ) ?? row.short,
      color: here.get(row.key)?.color ?? row.color,
      lean: row.lean,
      seats: row.current?.seats ?? 0,
      here: here.get(row.key)?.current?.seats ?? 0,
      members: holding?.seats.get(row.key) ?? [],
    })),
  };
}

async function tally(
  contest: Found,
  areaK: number,
  round: number | null,
  last: number,
  lineage: Array<Relation>,
  match: Match,
  grouping: Grouping,
  seats: number | null,
) {
  if (round == null) {
    return null;
  }
  const rounds = Array.from({ length: last }, (_, index) => index + 1);
  const [sides, spent, system] = await Promise.all([
    Promise.all(
      rounds.map(async (step) =>
        load(
          [contest.root],
          [areaK],
          contest.date ?? "infinity",
          seats != null && step === last ? [contest.id] : [],
          [],
          step,
        ),
      ),
    ),
    db.execute<{ measure: number; round: number; value: number }>(sql`
      select r.measure, r.count_round as round, sum(r.value)::float8 as value
      from result_set rs cross join lateral (
        select x.measure, x.count_round, x.value from result_rollup x
        where x.result_set_k = rs.k and x.area_k = ${areaK} and x.option_k = 0 and x.measure in (21, 26) offset 0
      ) r
      where rs.contest_id = ${contest.id}::uuid and ${latest("rs")}
      group by r.measure, r.count_round
      order by r.count_round
    `),
    db.execute<{ isSupplementary: boolean }>(sql`
      select coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula', '') = 'supplementary' as "isSupplementary"
      from contest c left join electoral_system es on es.id = c.electoral_system_id
      where c.id = ${contest.root}::uuid
    `),
  ]);
  const lines = new Map<
    string,
    {
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      person: string | null;
      party: string | null;
      outcome: string | null;
      votes: Array<number | null>;
    }
  >();
  const steps = sides.map((entry) => {
    const side = entry.get(areaK)?.side;
    return side == null ? [] : compare(side, null, lineage, match, grouping);
  });
  const personal = new Set(
    steps
      .flat()
      .filter((row) => row.key.startsWith("person:"))
      .map((row) => row.current?.party),
  );
  for (const [index, compared] of steps.entries()) {
    for (const found of compared) {
      const row =
        found.key.startsWith("party:") &&
        found.current?.person != null &&
        personal.has(found.current.party)
          ? { ...found, key: `person:${found.current.person}` }
          : found;
      const line = lines.get(row.key) ?? {
        key: row.key,
        label: row.label,
        short: row.short,
        color: row.color,
        person: row.current?.person ?? null,
        party: row.current?.party ?? null,
        outcome: null,
        votes: rounds.map(() => null),
      };
      line.votes[index] = row.current?.votes ?? null;
      line.outcome = row.current?.outcome ?? line.outcome;
      lines.set(row.key, line);
    }
  }
  const reach = (votes: Array<number | null>) =>
    votes.findLastIndex((value) => value != null);
  const exhausted = new Map(
    spent.rows
      .filter((row) => row.measure === 21)
      .map((row) => [row.round, row.value]),
  );
  const declared = spent.rows.find((row) => row.measure === 26)?.value;
  const automatic = lines
    .values()
    .filter(
      (line) =>
        line.votes[0] == null &&
        ["elected", "won"].includes(line.outcome ?? ""),
    )
    .toArray().length;
  const filled = seats == null ? null : seats - automatic;
  const listed = lines
    .values()
    .filter((line) => seats == null || line.votes[0] != null)
    .toArray()
    .map((line, _, all) =>
      line.short != null &&
      all.some((other) => other !== line && other.label === line.label)
        ? { ...line, label: `${line.label} (${line.short})` }
        : line,
    );
  let first = 0;
  for (const line of listed) {
    first += line.votes[0] ?? 0;
  }
  const quota = declared ?? Math.floor(first / ((filled ?? 0) + 1)) + 1;
  return {
    round,
    last,
    initial: seats == null ? last : 1,
    rows: listed.toSorted(
      (a, b) =>
        reach(b.votes) - reach(a.votes) ||
        (b.votes[reach(b.votes)] ?? 0) - (a.votes[reach(a.votes)] ?? 0),
    ),
    exhausted: rounds.map((step) => exhausted.get(step) ?? null),
    isSupplementary: system.rows[0]?.isSupplementary ?? false,
    stv:
      filled == null
        ? null
        : {
            seats: filled,
            quota,
            isDerived: declared == null,
            steps: rounds.map((step) => {
              const from =
                step === 1
                  ? []
                  : listed.filter(
                      (line) =>
                        line.votes[step - 2] != null &&
                        (line.votes[step - 1] ?? 0) <
                          (line.votes[step - 2] ?? 0) - 0.5,
                    );
              return {
                kind:
                  step === 1
                    ? ("first" as const)
                    : from.length === 0
                      ? ("none" as const)
                      : from.every(
                            (line) => (line.votes[step - 2] ?? 0) >= quota,
                          )
                        ? ("surplus" as const)
                        : ("exclusion" as const),
                from: from.map((line) => line.key),
                elected: listed
                  .filter(
                    (line) =>
                      ["elected", "won"].includes(line.outcome ?? "") &&
                      ((line.votes[step - 1] ?? 0) >= quota
                        ? step === 1 || (line.votes[step - 2] ?? 0) < quota
                        : step === last &&
                          line.votes.every((value) => (value ?? 0) < quota)),
                  )
                  .map((line) => line.key),
              };
            }),
          },
  };
}

async function places(
  contest: Found,
  area: { id: string; k: number },
  isRoot: boolean,
  base: string | null,
  by: string | undefined,
  chosen: { id: string; date: string | null } | null,
  lineage: Array<Relation>,
  match: Match,
  grouping: Grouping = "option",
  round: number | null = null,
): Promise<v.InferOutput<typeof Places>> {
  const kids = await children(
    contest.id,
    area,
    isRoot,
    contest.date ?? "infinity",
    undefined,
    true,
  );
  const counts = new Map<
    string,
    {
      code: string;
      noun: string;
      kind: string;
      rank: number;
      count: number;
      isDeep: boolean;
    }
  >();
  for (const kid of kids) {
    const level = counts.get(kid.scheme) ?? {
      code: kid.scheme,
      noun: kid.noun,
      kind: kid.kind,
      rank: kid.rank,
      count: 0,
      isDeep: kid.isDeep,
    };
    level.count += 1;
    counts.set(kid.scheme, level);
  }
  const eligible = counts
    .values()
    .filter((level) => level.count > 1)
    .toArray();
  if (eligible.length === 0) {
    const sets = await db.execute<{ k: number; isDefault: boolean }>(sql`
      with recursive grp(id) as (
        select ${contest.id}::uuid
        union all
        select c.id from contest c join grp on c.allocation_parent_id = grp.id
      )
      select rs.k, rs.is_default as "isDefault" from grp cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs
      where rs.status = 'active'
    `);
    const defaults = sets.rows
      .filter((row) => row.isDefault)
      .map((row) => row.k);
    const within = [area.id, ...kids.map((kid) => kid.id)];
    const units = await db.execute<{
      k: number;
      id: string;
      code: string;
      scheme: string;
      noun: string;
      kind: (typeof POOLS)[number];
      channel: number | null;
      isDefault: boolean;
    }>(sql`
      select u.k, u.id, u.code, u.scheme_code as scheme, s.noun, ${POOL} as kind,
        (select v.channel from result_value v where v.result_set_k = any(${sql.param(sets.rows.map((row) => row.k))}::int[]) and v.unit_k = u.k and v.measure = any(${COUNTED}) and v.option_k <> 0 limit 1) as channel,
        exists (
          select from result_value v
          where v.result_set_k = any(${sql.param(defaults)}::int[]) and v.unit_k = u.k
            and v.measure = any(${COUNTED})
        ) as "isDefault"
      from reporting_unit u join scheme s on s.code = u.scheme_code
      where u.k in (
          select x.k from reporting_unit x where x.area_relation = 'within'
            and (x.area_id = any(${sql.param(within)}::uuid[]) or x.id in (select m.unit_id from reporting_unit_area m where m.area_id = any(${sql.param(within)}::uuid[])))
          union
          select v.unit_k from result_value v join reporting_unit x on x.k = v.unit_k and x.area_id is null and x.kind <> 'standard'
          where v.result_set_k = any(${sql.param(sets.rows.map((row) => row.k))}::int[])
        )
        and exists (
          select from result_value v
          where v.result_set_k = any(${sql.param(sets.rows.map((row) => row.k))}::int[]) and v.unit_k = u.k
            and v.measure = any(${COUNTED})
        )
    `);
    let schemes: Array<typeof units.rows> = [];
    for (const pool of [
      units.rows.filter((row) => row.isDefault),
      units.rows,
    ]) {
      schemes = Map.groupBy(pool, (row) => row.scheme)
        .values()
        .filter((group) => group.length > 1)
        .toArray()
        .toSorted((a, b) => b.length - a.length);
      if (schemes.length > 0) {
        break;
      }
    }
    const group =
      schemes.find((entry) => `unit:${entry[0]?.scheme ?? ""}` === by) ??
      schemes[0];
    if (group == null) {
      return null;
    }
    const labels = await names(
      group.map((row) => row.id),
      contest.date,
    );
    const isMixed = group.some((row) => row.kind === "standard");
    const rows = group
      .map((row) => {
        const isPool =
          isMixed && !["standard", "mobile", "prison"].includes(row.kind);
        const segments = row.code.split(":");
        const code =
          row.scheme === "us.precinct" && segments.length > 4
            ? segments.slice(4).join(":")
            : (segments.at(-1) ?? row.code);
        const cased = /\p{Ll}/u.test(code)
          ? code
          : code.replaceAll(/\p{Lu}+/gu, (word) =>
              word.length > 2
                ? word.charAt(0) + word.slice(1).toLowerCase()
                : ["OF", "AT", "IN", "ON", "BY", "TO"].includes(word)
                  ? word.toLowerCase()
                  : word,
            );
        const noun = row.noun.charAt(0).toUpperCase() + row.noun.slice(1);
        const pooled = (
          POOLED_CHANNELS[row.channel ?? 0] ?? POOLED_NAMES[row.kind]
        )();
        const ordinal = /\d+$/u.exec(code)?.[0];
        return {
          k: row.k,
          name:
            labels.full.get(row.id) ??
            (isPool
              ? ordinal == null
                ? pooled
                : `${pooled} ${ordinal.replace(/^0+(?=\d)/u, "")}`
              : /^\d/u.test(cased)
                ? `${noun} ${cased.replace(/^0+(?=\d)/u, "")}`
                : cased),
          path: null,
          isContest: false,
          owner: null,
          isPool,
        };
      })
      .toSorted(
        (a, b) =>
          Number(a.isPool) - Number(b.isPool) ||
          a.name.localeCompare(b.name, undefined, { numeric: true }),
      );
    const tabulated = await tabulate(
      [contest.root],
      rows,
      contest.date,
      null,
      lineage,
      match,
      grouping,
      round,
      "unit",
    );
    return {
      levels: schemes.map((entry) => ({
        code: `unit:${entry[0]?.scheme ?? ""}`,
        noun: entry[0]?.noun ?? "",
        count: entry.length,
      })),
      level: `unit:${group[0]?.scheme ?? ""}`,
      ...tabulated,
      rows: tabulated.rows.map((row, index) => ({
        ...row,
        at: null,
        ...(rows[index]?.isPool === true && { isPool: true }),
      })),
    };
  }
  const allocated = await db.execute<{
    area: string;
    k: number;
    parent: string | null;
    path: string | null;
    seats: number | null;
  }>(sql`
    select c.district_area_id as area, a.k, p.parent_id as parent, s.path,
      (
        select sum(x.seats)::int from contest_option o
        cross join lateral (
          select max(x.seats) as seats from outcome x
          join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
          where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          having count(*) > 0
        ) x
        where o.contest_id = c.id
      ) as seats
    from contest c
    join area a on a.id = c.district_area_id
    left join area_parent p on p.area_id = a.id and p.valid @> ${contest.date ?? "infinity"}::date
    left join slug s on s.entity_id = c.id and s.is_current
    where c.allocation_parent_id = ${contest.id}::uuid
  `);
  const childPath = new Map(allocated.rows.map((row) => [row.area, row.path]));
  const joined = new Set(
    kids.filter((kid) => kid.isMember).map((kid) => kid.scheme),
  );
  const checked = kids.filter(
    (kid) =>
      !kid.isDeep &&
      kid.scheme !== "world.country" &&
      eligible.some((level) => level.code === kid.scheme),
  );
  const straddled =
    checked.length > 600
      ? null
      : await db.execute<{ k: number }>(sql`
        with me as materialized (
          select geom from area_geometry where area_id = ${area.id}::uuid
          order by valid @> ${contest.date ?? "infinity"}::date desc, upper(valid) desc nulls first limit 1
        ),
        straddling as materialized (
          select a.id, a.k from (
            select a.id, a.k from area a
            where a.k = any(${sql.param(checked.map((kid) => kid.k))}::int[])
              and not exists (
                select from area_parent p
                where p.area_id = a.id and p.parent_id = ${area.id}::uuid and p.valid @> ${contest.date ?? "infinity"}::date
              )
            offset 0
          ) a
          cross join me
          cross join lateral (
            select g.geom from area_geometry g where g.area_id = a.id
            order by g.valid @> ${contest.date ?? "infinity"}::date desc, upper(g.valid) desc nulls first limit 1
          ) g
          where ST_Area(ST_Intersection(g.geom, me.geom)) < 0.95 * ST_Area(g.geom)
        ),
        siblings as materialized (
          select rs.k from contest own
          join contest c on c.election_id = own.election_id and c.id <> own.id
            and (c.office_id = own.office_id or c.body_id = own.body_id)
          join result_set rs on rs.contest_id = c.id and rs.status = 'active'
          where own.id = ${contest.id}::uuid
        )
        select s.k from straddling s
        where not exists (
            select from reporting_unit ru
            join result_value v on v.unit_k = ru.k
            join result_set rs on rs.k = v.result_set_k and rs.contest_id = ${contest.id}::uuid
            where ru.area_id = s.id and ru.area_relation = 'equals'
          )
          or exists (select from siblings x join result_rollup_unit u on u.result_set_k = x.k and u.area_k = s.k)
          or exists (select from siblings x join result_rollup_sum r on r.result_set_k = x.k and r.area_k = s.k)
      `);
  const split = new Set(straddled?.rows.map((row) => row.k) ?? []);
  const partial = new Set(
    checked.filter((kid) => split.has(kid.k)).map((kid) => kid.scheme),
  );
  const levels = eligible.toSorted(
    (a, b) =>
      Number(a.isDeep) - Number(b.isDeep) ||
      Number(partial.has(a.code)) - Number(partial.has(b.code)) ||
      Number(joined.has(a.code)) - Number(joined.has(b.code)) ||
      a.rank - b.rank ||
      Number(
        kids.some((kid) => kid.scheme === b.code && childPath.has(kid.id)),
      ) -
        Number(
          kids.some((kid) => kid.scheme === a.code && childPath.has(kid.id)),
        ) ||
      Number(b.kind === "administrative") -
        Number(a.kind === "administrative") ||
      b.count - a.count,
  );
  const tiled = await db.execute<{ code: string }>(sql`
    select distinct bs.scheme_code as code from boundary_set bs
    where bs.scheme_code = any(${sql.param(levels.map((entry) => entry.code))}::text[])
      and (bs.id = (select boundary_set_id from contest where id = ${contest.id}::uuid) or bs.valid @> ${contest.date ?? "infinity"}::date)
  `);
  const level =
    levels.find((entry) => entry.code === by) ??
    levels.find((entry) => tiled.rows.some((row) => row.code === entry.code)) ??
    levels[0];
  if (level == null) {
    return null;
  }
  const shown = kids.filter((kid) => kid.scheme === level.code);
  const tail = /, [^,]+$/.exec(shown[0]?.name ?? "")?.[0];
  const isShared =
    tail != null && shown.every((kid) => kid.name.endsWith(tail));
  const rows = shown.map((kid) => {
    const child = childPath.get(kid.id);
    const name = isShared ? kid.name.slice(0, -tail.length) : kid.name;
    return {
      k: kid.k,
      name: split.has(kid.k) ? m.map_part({ name }) : name,
      path: child ?? (base == null ? null : `${base}/${kid.segment}`),
      isContest: child != null,
      owner: null,
    };
  });
  const pooled = await db.execute<{
    pool: (typeof POOLS)[number];
    isPlaced: boolean;
    channels: Array<number>;
  }>(sql`
    with recursive grp(id) as (
      select ${contest.root}::uuid
      union all
      select c.id from contest c join grp on c.allocation_parent_id = grp.id
    ),
    sets as materialized (
      select rs.k from grp join result_set rs on rs.contest_id = grp.id and rs.status = 'active'
    ),
    short as materialized (
      select ${isRoot && contest.place?.k === area.k ? sql`true` : sql`false`} and (
        select sum(r.value) from sets cross join lateral (
          select r.value from result_rollup r
          where r.result_set_k = sets.k and r.area_k = ${area.k} and r.measure = any(${COUNTED}) and r.option_k <> 0 offset 0
        ) r
      ) > (
        select coalesce(sum(r.value), 0) from sets cross join unnest(${sql.param(shown.map((kid) => kid.k))}::int[]) as kid(k) cross join lateral (
          select r.value from result_rollup r
          where r.result_set_k = sets.k and r.area_k = kid.k and r.measure = any(${COUNTED}) and r.option_k <> 0 offset 0
        ) r
      ) as "isShort"
    )
    select u.pool, u."isPlaced", array_agg(distinct u.channel) as channels from (
      select ${POOL} as pool, false as "isPlaced", (
        select v.channel from sets cross join lateral (select x.channel from result_value x where x.result_set_k = sets.k and x.unit_k = u.k and x.measure = any(${COUNTED}) and x.option_k <> 0 limit 1) v limit 1
      ) as channel
      from jsonb_array_elements_text(${JSON.stringify(
        await remember("unlocated", async () =>
          Object.fromEntries(
            (
              await db.execute<{ polity: string; units: Array<number> }>(sql`
                with recursive up(k, id) as (
                  select u.k, u.polity_id from reporting_unit u where u.area_id is null
                  union
                  select up.k, p.parent_id from polity_parent p join up on p.polity_id = up.id
                )
                select id::text as polity, array_agg(k order by k) as units from up group by id
              `)
            ).rows.map((row) => [row.polity, row.units]),
          ),
        ),
      )}::jsonb -> (
        select e.polity_id::text from contest c join election e on e.id = c.election_id where c.id = ${contest.root}::uuid
      )) as uk(k)
      join reporting_unit u on u.k = uk.k::int
      where (select "isShort" from short) and ${POOLED}
        and exists (select from sets cross join lateral (select from result_value x where x.result_set_k = sets.k and x.unit_k = u.k limit 1) v)
      union all
      select ${POOL}, true, (
        select v.channel from sets cross join lateral (select x.channel from result_value x where x.result_set_k = sets.k and x.unit_k = u.k and x.measure = any(${COUNTED}) and x.option_k <> 0 limit 1) v limit 1
      )
      from reporting_unit u
      where u.area_id = ${area.id}::uuid and ${PLACED}
        and exists (select from sets cross join lateral (select from result_value x where x.result_set_k = sets.k and x.unit_k = u.k limit 1) v)
    ) u
    group by 1, 2
  `);
  const pools = pooled.rows
    .filter(() => level.code !== "world.country")
    .map((row) => ({ ...row, index: POOLS.indexOf(row.pool) }))
    .toSorted(
      (a, b) => Number(a.isPlaced) - Number(b.isPlaced) || a.index - b.index,
    )
    .map((row) => ({
      k: -((row.isPlaced ? area.k * 16 : 0) + row.index + 1),
      name: (
        (row.index >= POOLS.indexOf("aggregate") && row.channels.length === 1
          ? POOLED_CHANNELS[row.channels[0] ?? 0]
          : null) ?? POOLED_NAMES[row.pool]
      )(),
      path: null,
      isContest: false,
      isCounted: false,
      owner: null,
    }));
  const parents = new Map(shown.map((kid) => [kid.id, kid.k]));
  const nested = allocated.rows.filter(
    (row) =>
      !parents.has(row.area) && row.parent != null && parents.has(row.parent),
  );
  const nestedNames = await names(
    nested.map((row) => row.area),
    contest.date,
  );
  const listed = [
    ...rows,
    ...pools,
    ...nested.map((row) => ({
      k: row.k,
      name: nestedNames.full.get(row.area) ?? "",
      path: row.path,
      isContest: true,
      owner: null,
    })),
  ];
  const crosswalk =
    chosen == null
      ? null
      : await db.execute<{ from_k: number; to_k: number; weight: number }>(sql`
        select w.from_k, w.to_k, w.weight::float8 as weight
        from crosswalk x
        join crosswalk_weight w on w.crosswalk_id = x.id
        where x.to_boundary_set_id = (
            select id from boundary_set
            where scheme_code = ${level.code}
              and (id = (select boundary_set_id from contest where id = ${contest.id}::uuid) or valid @> ${contest.date ?? "infinity"}::date)
            order by id = (select boundary_set_id from contest where id = ${contest.id}::uuid) desc, valid_from desc nulls last
            limit 1
          )
          and x.from_boundary_set_id = (
            select id from boundary_set
            where scheme_code = ${level.code} and valid @> ${chosen.date ?? "infinity"}::date
            order by valid_from desc nulls last
            limit 1
          )
          and w.to_k = any(${sql.param(listed.map((row) => row.k))}::int[])
          and not exists (
            select from crosswalk y
            where y.from_boundary_set_id = x.from_boundary_set_id and y.to_boundary_set_id = x.to_boundary_set_id
              and y.method = 'population' and x.method <> 'population'
          )
      `);
  const weights = crosswalk?.rows ?? [];
  const spread = new Map<number, Array<[number, number]>>();
  for (const row of weights) {
    const list = spread.get(row.from_k) ?? [];
    list.push([row.to_k, row.weight]);
    spread.set(row.from_k, list);
  }
  const [counted, at, moved] = await Promise.all([
    tabulate(
      [contest.root],
      listed,
      contest.date,
      chosen == null
        ? null
        : {
            ids: [chosen.id],
            date: chosen.date,
            ...(spread.size > 0 && { spread }),
          },
      lineage,
      match,
      grouping,
      round,
    ),
    anchors(
      listed.map((row) => row.k),
      contest.date ?? "infinity",
    ),
    redrawn(
      [...shown.map((kid) => kid.id), ...nested.map((row) => row.area)].filter(
        () => spread.size === 0,
      ),
      contest,
      chosen,
      area.id,
    ),
  ]);
  const tabulated = {
    ...counted,
    rows: counted.rows.map((row) => {
      const overlap = moved.get(row.k);
      return overlap == null
        ? row
        : { ...row, earlier: null, prior: null, redrawn: overlap };
    }),
  };
  const [turned, prior, whole] = await Promise.all([
    turnout(
      contest.root,
      listed.map((row) => row.k),
    ),
    turnout(
      chosen?.id ?? null,
      listed.map((row) => row.k),
    ),
    load(
      [contest.root],
      [area.k],
      contest.date ?? "infinity",
      [],
      [],
      round,
      "area",
      null,
      false,
      counted.sets,
    ),
  ]);
  const seats = new Map(allocated.rows.map((row) => [row.k, row.seats]));
  const contexts =
    level.code === "world.country"
      ? (
          await db.execute<{ id: string; k: number; body: string }>(sql`
            select a.id, a.k, n.body_md as body from area a join note n on n.entity_id = a.id and n.kind = 'context'
            where a.scheme_code = 'world.country'
          `)
        ).rows
      : [];
  const statuses = new Map(contexts.map((row) => [row.k, row.body]));
  const placed = new Set(shown.map((kid) => kid.k));
  const unplaced = contexts.filter((row) => !placed.has(row.k));
  const called =
    unplaced.length === 0
      ? null
      : await names(
          unplaced.map((row) => row.id),
          contest.date,
        );
  const turnouts = (row: (typeof tabulated.rows)[number]) => {
    const entry = turned.get(row.k);
    const now = share(entry, row.valid);
    return {
      turnout:
        now == null || entry == null
          ? null
          : ([entry.voters, entry.registered] as [number, number]),
      turnedBefore:
        now == null || row.earlier == null
          ? null
          : share(prior.get(row.k), prior.get(row.k)?.voters ?? 0),
    };
  };
  const scoped = await db.execute<Record<string, never>>(sql`
    select from contest where id = ${contest.id}::uuid and party_scope_id is not null
  `);
  const waiting =
    scoped.rows.length === 0
      ? []
      : (
          await universe(
            contest.id,
            level.code,
            area.id,
            contest.date ?? "infinity",
          )
        ).filter((row) => row.pending && !shown.some((kid) => kid.k === row.k));
  const waited =
    waiting.length === 0
      ? null
      : await names(
          waiting.map((row) => row.id),
          contest.date,
        );
  return {
    levels: levels.map((entry) => ({
      code: entry.code,
      noun: entry.noun,
      count: entry.count,
    })),
    level: level.code,
    ...tabulated,
    total: whole.get(area.k)?.side.valid,
    estimated: spread.size > 0,
    ...(waited != null && {
      pending: waiting
        .map((row) => waited.full.get(row.id) ?? "")
        .toSorted((a, b) => a.localeCompare(b)),
    }),
    ...(called != null && {
      noted: unplaced.map((row) => ({
        k: row.k,
        name: called.full.get(row.id) ?? "",
        status: row.body,
      })),
    }),
    rows: tabulated.rows
      .slice(0, rows.length + pools.length)
      .map((row) => ({
        ...row,
        ...turnouts(row),
        at: at.get(row.k) ?? null,
        seats: seats.get(row.k) ?? null,
        ...(row.k < 0 && { isPool: true }),
        ...(statuses.has(row.k) && { status: statuses.get(row.k) }),
      })),
    districts: tabulated.rows
      .slice(rows.length + pools.length)
      .map((row, index) => ({
        ...row,
        ...turnouts(row),
        at: at.get(row.k) ?? null,
        seats: seats.get(row.k) ?? null,
        within: parents.get(nested[index]?.parent ?? ""),
      })),
  };
}

async function tabulate(
  roots: Array<string>,
  rows: Array<{
    k: number;
    name: string;
    path: string | null;
    isContest: boolean;
    isRemainder?: boolean;
    isCounted?: boolean;
    owner: string | null;
    isApart?: boolean;
  }>,
  date: string | null,
  chosen: {
    ids: Array<string>;
    date: string | null;
    pairs?: Array<{ k: number; id: string }>;
    spread?: Map<number, Array<[number, number]>>;
  } | null,
  lineage: Array<Relation>,
  match: Match,
  grouping: Grouping = "option",
  round: number | null = null,
  facts: keyof typeof FACTS = "area",
) {
  const ks = rows.map((row) => row.k);
  const depth = new Map<number, number>();
  const stacked = rows.map((row) => {
    const n = depth.get(row.k) ?? 0;
    depth.set(row.k, n + 1);
    return n;
  });
  const top = Math.max(0, ...stacked) + 1;
  const tier = stacked.map((n, index) => ((rows[index]?.k ?? 0) < 0 ? top : n));
  const [now, then] = await Promise.all([
    Promise.all(
      Array.from({ length: top + 1 }, async (_, n) => {
        const batch = rows.filter((_row, index) => tier[index] === n);
        return load(
          roots,
          batch.map((row) => row.k),
          date ?? "infinity",
          [],
          batch.flatMap((row) => (row.owner == null ? [] : [row.owner])),
          round,
          n === top ? "pool" : facts,
        );
      }),
    ),
    chosen == null
      ? null
      : load(
          chosen.ids,
          chosen.spread == null
            ? (chosen.pairs?.map((pair) => pair.k) ?? ks)
            : chosen.spread.keys().toArray(),
          chosen.date ?? "infinity",
          [],
          chosen.spread == null ? chosen.pairs?.map((pair) => pair.id) : [],
          null,
          "area",
          chosen.spread ?? null,
          round === 1,
        ),
  ]);
  const legend = new Map<
    string,
    {
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      person: string | null;
      party: string | null;
      votes: number;
      lead: boolean;
      people: Set<string>;
    }
  >();
  const sources = new Set<string>();
  const unreconciled = new Map<
    string,
    v.InferOutput<typeof Unreconciled> & { publisher: string }
  >();
  const owners = rows.flatMap((row) =>
    row.isContest && row.owner != null ? [row.owner] : [],
  );
  const elected =
    owners.length === 0
      ? null
      : await db.execute<{
          contest: string;
          person: string | null;
          party: string | null;
          isIndependent: boolean;
          seats: number;
          isStv: boolean;
        }>(sql`
        select c.id as contest, coalesce(m.into_id, cy.person_id) as person, coalesce(pm.into_id, o.party_id) as party,
          coalesce(cy.is_independent, false) as "isIndependent", w.seats,
          coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula', '') = 'stv' as "isStv"
        from contest c
        left join electoral_system es on es.id = c.electoral_system_id
        join result_set rs on rs.contest_id = c.id and ${latest("rs")}
        join contest_option o on o.contest_id = c.id
        cross join lateral (select ${won(sql`rs.id`, sql`o.id`)} as seats) w
        left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
        left join entity_merge m on m.entity_id = cy.person_id
        left join entity_merge pm on pm.entity_id = o.party_id
        where c.id = any(${sql.param(owners)}::uuid[]) and c.magnitude > 1 and w.seats > 0
      `);
  const chosenOnes = new Set(
    elected?.rows.flatMap((row) =>
      row.person == null ? [] : [`${row.contest}:${row.person}`],
    ),
  );
  const seatsWon = Map.groupBy(elected?.rows ?? [], (row) => row.contest);
  const transferred = new Set(
    elected?.rows.flatMap((row) => (row.isStv ? [row.contest] : [])),
  );
  const elects = rows.map((row, index) => {
    const side = row.isContest
      ? now[tier[index] ?? 0]?.get(row.k)?.side
      : undefined;
    const options = side?.options ?? [];
    const won = options.filter((option) =>
      chosenOnes.has(`${option.contest}:${option.person}`),
    );
    const held = new Map<string, number>();
    for (const entry of row.isContest
      ? (seatsWon.get(row.owner ?? "") ?? [])
      : []) {
      const key =
        entry.party != null
          ? `party:${entry.party}`
          : entry.isIndependent
            ? "independent"
            : `person:${entry.person}`;
      held.set(key, (held.get(key) ?? 0) + entry.seats);
    }
    let split: number | null = null;
    if (side != null && won.length > 1 && side.valid > 0) {
      let [last, next] = [Infinity, 0];
      for (const option of options) {
        if (won.includes(option)) {
          last = Math.min(last, option.votes);
        } else if (option.kind === "candidate") {
          next = Math.max(next, option.votes);
        }
      }
      split = (last - next) / side.valid;
    }
    return {
      isMulti:
        won.length > 1 ||
        held.size > 1 ||
        held.values().some((count) => count > 1),
      held,
      split,
    };
  });
  const results = rows.map((row, index) => {
    const current = now[tier[index] ?? 0]?.get(row.k);
    const held = elects[index]?.held ?? new Map<string, number>();
    return current == null
      ? []
      : compare(
          current.side,
          then?.get(row.k)?.side ?? null,
          lineage,
          match,
          elects[index]?.isMulti === true ? "party" : grouping,
        ).toSorted((a, b) => (held.get(b.key) ?? 0) - (held.get(a.key) ?? 0));
  });
  const personal = new Set(
    results.flatMap((entries, index) =>
      entries
        .filter((entry) => entry.key.startsWith("person:"))
        .map(
          (entry) =>
            `${rows[index]?.isApart === true ? index : (rows[index]?.owner ?? "")}:${(entry.current ?? entry.earlier)?.party}`,
        ),
    ),
  );
  const compared = rows.map((row, index) => {
    const current = now[tier[index] ?? 0]?.get(row.k);
    const owner = row.isApart === true ? index : (row.owner ?? "");
    const sets = current?.sets ?? [];
    for (const set of sets) {
      sources.add(set.source.publisher);
      if (set.unreconciled != null) {
        unreconciled.set(JSON.stringify(set.unreconciled), {
          ...set.unreconciled,
          publisher: set.source.publisher,
        });
      }
    }
    const result = (results[index] ?? []).map((entry) =>
      entry.key.startsWith("party:") &&
      entry.current?.person != null &&
      personal.has(`${owner}:${entry.current.party}`)
        ? { ...entry, key: `person:${entry.current.person}` }
        : entry,
    );
    const running = result.find(
      (entry) => entry.current != null && !entry.key.startsWith("kind:"),
    );
    const lumped = result.find(
      (entry) =>
        entry.key.startsWith("kind:others") &&
        (entry.current?.votes ?? 0) > (running?.current?.votes ?? 0),
    );
    const leader =
      lumped ?? ((running?.current?.votes ?? 0) > 0 ? (running ?? null) : null);
    const seated = elects[index]?.held;
    const seats = new Map(
      result.map((entry, at) => [
        entry.key,
        seated?.get(results[index]?.[at]?.key ?? "") ?? 0,
      ]),
    );
    let before: (typeof result)[number] | null = null;
    for (const entry of result) {
      if (
        entry.earlier != null &&
        !entry.key.startsWith("kind:") &&
        entry.earlier.votes > (before?.earlier?.votes ?? -1)
      ) {
        before = entry;
      }
    }
    for (const entry of result) {
      const item = legend.get(entry.key) ?? {
        key: entry.key,
        label: entry.label,
        short: entry.key.startsWith("alliance:") ? null : entry.short,
        color: entry.color,
        person: null,
        party: null,
        votes: 0,
        lead: false,
        people: new Set<string>(),
      };
      const person = entry.current?.person;
      if (person != null) {
        item.people.add(person);
      }
      item.person ??= (entry.current ?? entry.earlier)?.person ?? null;
      item.party ??= (entry.current ?? entry.earlier)?.party ?? null;
      item.votes += entry.current?.votes ?? 0;
      item.lead ||=
        entry.key === leader?.key ||
        (seats.get(entry.key) ?? 0) > 0 ||
        (entry.key === before?.key && before.key !== leader?.key);
      legend.set(entry.key, item);
    }
    return {
      row,
      result,
      leader,
      seats,
      before,
      split: elects[index]?.split ?? null,
      valid: current?.side.valid ?? 0,
      isTally: current?.isTally === true,
    };
  });
  const silent = rows.flatMap((row, index) =>
    row.owner == null || now[tier[index] ?? 0]?.has(row.k) === true
      ? []
      : [row.owner],
  );
  const root = ancestry(lineage, "party");
  const declared = await db.execute<{
    contest: string;
    isRecorded: boolean;
    isTacit: boolean;
    kind: "elected" | "won" | null;
    party: string | null;
    person: string | null;
    color: string | null;
  }>(sql`
    select c.id as contest, rs.id is not null as "isRecorded", rs.kind is not distinct from 'tacit' as "isTacit",
      w.kind, w.party, w.person,
      (select pc.color from party_color pc where pc.party_id = w.party
        order by pc.valid @> e.date_from desc, upper(pc.valid) desc nulls first limit 1) as color
    from contest c
    join election e on e.id = c.election_id
    left join lateral (
      select rs.id, rs.kind::text as kind from result_set rs
      where rs.contest_id = c.id and ${latest("rs")} order by rs.stage desc limit 1
    ) rs on true
    left join lateral (
      select x.kind::text as kind, coalesce(pm.into_id, o.party_id) as party, coalesce(m.into_id, cy.person_id) as person
      from outcome x
      join contest_option o on o.id = x.option_id
      left join entity_merge pm on pm.entity_id = o.party_id
      left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
      left join entity_merge m on m.entity_id = cy.person_id
      where x.result_set_id = rs.id and x.kind in ('elected', 'won')
      order by x.seats desc nulls last, o.k limit 1
    ) w on true
    where c.id = any(${sql.param(silent)}::uuid[])
  `);
  const winners = await names(
    declared.rows.flatMap((row) =>
      [row.person, row.party].filter((id) => id != null),
    ),
    date,
  );
  const unopposed = new Map(
    declared.rows.map((row) => {
      const key = [
        row.party == null ? null : `party:${root(row.party)}`,
        row.person == null ? null : `person:${row.person}`,
      ].find((entry) => entry != null && legend.has(entry));
      const item = legend.get(key ?? "");
      if (item != null) {
        item.lead = true;
        if (row.person != null) {
          item.people.add(row.person);
        }
      }
      return [
        row.contest,
        {
          key: item == null ? null : (key ?? null),
          declared:
            row.kind == null
              ? null
              : {
                  kind: row.kind,
                  isTacit: row.isTacit,
                  label:
                    (row.person == null
                      ? null
                      : winners.full.get(row.person)) ??
                    (row.party == null
                      ? null
                      : (winners.short.get(row.party) ??
                        winners.full.get(row.party))) ??
                    "",
                  color: row.color,
                  person: row.person,
                  party: row.party,
                },
          isRecorded: row.isRecorded,
        },
      ] as const;
    }),
  );
  const ranked = legend
    .values()
    .filter((item) => item.votes > 0 || item.lead)
    .toArray()
    .toSorted((a, b) => b.votes - a.votes);
  const fields = new Map<string, number>();
  for (const { row, result } of compared) {
    for (const entry of result) {
      if (row.isContest && entry.current != null) {
        fields.set(entry.key, (fields.get(entry.key) ?? 0) + 1);
      }
    }
  }
  const isOwn = (key: string) =>
    key.startsWith("person:") && fields.get(key) === 1;
  const kept = ranked.filter(
    (item, index) => index < 8 || item.lead || isOwn(item.key),
  );
  const position = new Map(kept.map((item, index) => [item.key, index]));
  const relit = new Map(
    kept.flatMap((item) =>
      item.color == null ||
      rows.every((row) => !row.isContest) ||
      isOwn(item.key)
        ? []
        : [[item.key, item.color] as const],
    ),
  );
  separate(
    relit,
    kept.flatMap((item) =>
      isOwn(item.key) ? [] : [[item.key, item.votes] as const],
    ),
    new Map(),
    true,
  );
  return {
    isAllied: now.some((loaded) =>
      loaded
        .values()
        .some((entry) =>
          entry.side.options.some(
            (option) => option.alliance != null && option.votes > 0,
          ),
        ),
    ),
    legend: kept.map((item) => ({
      key: item.key,
      label: item.label,
      short: item.key.startsWith("person:") ? null : item.short,
      color: relit.get(item.key) ?? item.color,
      person:
        item.key.startsWith("person:") ||
        (item.people.size === 1 && rows.every((row) => !row.isContest))
          ? item.person
          : null,
      party: item.party,
    })),
    rows: compared.map(
      ({ row, result, leader, seats, before, split, valid, isTally }) => {
        const runner = result.find(
          (entry) =>
            entry.current != null &&
            !entry.key.startsWith("kind:") &&
            entry.key !== leader?.key,
        );
        const absent =
          valid > 0
            ? kept.flatMap((item, index) =>
                result.find((entry) => entry.key === item.key)?.current == null
                  ? [index]
                  : [],
              )
            : [];
        return {
          k: row.k,
          name: row.name,
          path: row.path,
          contest: row.isContest,
          isRemainder: row.isRemainder ?? false,
          isCounted: row.isCounted,
          ...(isTally && { isTally }),
          valid,
          shares: kept.map(
            (item) =>
              result.find((entry) => entry.key === item.key)?.current?.share ??
              0,
          ),
          absent: absent.length > 0 ? absent : undefined,
          leader:
            position.get(
              leader?.key ?? unopposed.get(row.owner ?? "")?.key ?? "",
            ) ?? null,
          held: holdings(seats, position),
          declared: unopposed.get(row.owner ?? "")?.declared ?? null,
          isRecorded: unopposed.get(row.owner ?? "")?.isRecorded ?? true,
          margin:
            leader?.current == null || transferred.has(row.owner ?? "")
              ? null
              : (split ?? leader.current.share - (runner?.current?.share ?? 0)),
          earlier:
            (then?.get(row.k)?.side.valid ?? 0) === 0
              ? null
              : kept.map(
                  (item) =>
                    result.find((entry) => entry.key === item.key)?.earlier
                      ?.share ?? 0,
                ),
          prior: before == null ? null : (position.get(before.key) ?? null),
        };
      },
    ),
    sources: [...sources],
    unreconciled: unreconciled.values().toArray(),
    sets: [
      ...new Set(
        now.flatMap((found) =>
          found
            .values()
            .flatMap((entry) => entry.ks)
            .toArray(),
        ),
      ),
    ],
  };
}

type Note = {
  code:
    | "no_target"
    | "not_covered"
    | "different_race"
    | "annulled"
    | "partial"
    | "alternative"
    | "redrawn"
    | "channels";
  side: "current" | "target" | null;
  value: string | null;
};

function note(code: Note["code"]): Note {
  return { code, side: null, value: null };
}

function caveats(
  side: "current" | "target",
  sets: Array<{
    stage: string;
    completeness: number | null;
    source: { publisher: string };
  }>,
) {
  const notes: Array<Note> = [];
  const partial = Math.min(...sets.map((set) => set.completeness ?? 1));
  if (partial < 1) {
    notes.push({ code: "partial", side, value: String(partial) });
  }
  const alternative = sets.find((set) => set.stage === "alternative");
  if (alternative != null) {
    notes.push({
      code: "alternative",
      side,
      value: alternative.source.publisher,
    });
  }
  return notes;
}

async function pick(
  covering: Array<{
    id: string;
    series: string;
    election: string;
    date: string | null;
    year: number | null;
    status: string;
  }>,
  contest: { series: string; date: string | null; fence: string | null },
  vs: string | undefined,
) {
  if (vs == null || vs === "" || vs === "none") {
    return { chosen: null, notes: [] };
  }
  if (vs === "previous") {
    const chosen =
      covering.find(
        (row) =>
          row.series === contest.series &&
          row.date != null &&
          contest.date != null &&
          row.date < contest.date &&
          (contest.fence == null || row.date > contest.fence) &&
          row.status !== "annulled" &&
          row.status !== "canceled",
      ) ?? null;
    return { chosen, notes: chosen == null ? [note("no_target")] : [] };
  }
  const wanted = await db.execute<{ id: string }>(sql`
    select e.id from slug s join entity e on e.id = s.entity_id where s.path = ${vs}
    union all
    select e.id from entity e where ${/^[0-9a-f-]{36}$/u.test(vs) ? vs : null}::uuid = e.id
  `);
  const entity = wanted.rows[0];
  if (entity == null) {
    return { chosen: null, notes: [note("not_covered")] };
  }
  const up = await db.execute<{ id: string }>(sql`
    with recursive up(id, parent) as (
      select id, allocation_parent_id from contest where id = ${entity.id}::uuid
      union all
      select c.id, c.allocation_parent_id from contest c join up on c.id = up.parent
    )
    select id from up where parent is null
  `);
  const rootId = up.rows[0]?.id;
  const chosen =
    covering.find((row) => row.id === rootId) ??
    covering
      .filter((row) => row.election === entity.id)
      .toSorted(
        (a, b) =>
          Number(b.series === contest.series) -
          Number(a.series === contest.series),
      )[0] ??
    null;
  if (chosen == null) {
    return { chosen, notes: [note("not_covered")] };
  }
  return {
    chosen,
    notes: [
      ...(chosen.series === contest.series ? [] : [note("different_race")]),
      ...(chosen.status === "annulled" ? [note("annulled")] : []),
    ],
  };
}

const Input = v.object({
  id: v.pipe(v.string(), v.uuid()),
  unit: v.optional(v.string()),
  by: v.optional(v.string()),
  vs: v.optional(v.string()),
  match: v.optional(v.picklist(["party", "person", "family"]), "party"),
  round: v.optional(v.pipe(v.number(), v.integer(), v.minValue(0)), 0),
  group: v.pipe(
    v.optional(v.string(), "option"),
    v.transform((value): Grouping =>
      value === "alliance" ? "alliance" : "option",
    ),
  ),
});

async function atlas(input: {
  id: string;
  unit?: string;
  level?: string;
  vs?: string;
  group: Grouping;
  isLean?: boolean;
  whole?: string;
}) {
  const picked = await find(input.id);
  const group =
    input.whole != null
      ? ((
          await db.execute<{
            ids: Array<string>;
            place: { id: string; k: number } | null;
            owner: string;
          }>(sql`
            with recursive down(id) as (
              select ${input.whole}::uuid
              union all
              select k.id from election k join down on k.parent_id = down.id
            )
            select array_agg(c.id order by c.id)::text[] as ids, (
              select json_build_object('id', a.id, 'k', a.k) from election e
              join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
              join area a on a.id = pt.area_id
              where e.id = ${input.whole}::uuid limit 1
            ) as place, coalesce(me.body_id, me.office_id) as owner
            from contest me
            join down on true
            join contest c on c.election_id = down.id
              and coalesce(c.body_id, c.office_id) = coalesce(me.body_id, me.office_id) and c.kind = me.kind
              and c.term is not distinct from me.term
              and not exists (select from contest pr join down d on d.id = pr.election_id where pr.id = c.previous_round_id)
              and c.allocation_parent_id is null and not c.notional and c.district_area_id is not null
            where me.id = ${picked.id}::uuid
            group by me.id
          `)
        ).rows[0] ?? null)
      : null;
  if (input.whole != null && (group == null || group.ids.length < 3)) {
    throw new ORPCError("NOT_FOUND");
  }
  const contest =
    group == null || input.whole == null
      ? picked
      : { ...picked, place: group.place, election: input.whole };
  const chain = await descend(contest, input.unit, group?.ids);
  const area = chain.at(-1) ?? contest.place;
  if (area == null) {
    throw new ORPCError("NOT_FOUND");
  }
  const day = contest.date ?? "infinity";
  const owners =
    group != null
      ? { rows: [] }
      : await db.execute<{
          id: string;
          place: string;
          depth: number;
          parts: number;
        }>(sql`
    with recursive anc(id, depth) as (
      select ${area.id}::uuid, 0
      union all
      select p.parent_id, anc.depth + 1 from area_parent p join anc on p.area_id = anc.id
      where p.valid @> ${day}::date and anc.depth < 8
    ),
    grp(id) as (
      select ${contest.root}::uuid
      union all
      select c.id from contest c join grp on c.allocation_parent_id = grp.id
    )
    select c.id, anc.id as place, anc.depth,
      (select count(*) from contest k where k.allocation_parent_id = c.id)::int as parts
    from grp
    cross join lateral (
      select c.id, c.allocation_parent_id, coalesce(c.district_area_id, (
        select pt.area_id from election e
        join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
        where e.id = c.election_id limit 1
      )) as area
      from contest c where c.id = grp.id offset 0
    ) c
    join anc on anc.id = c.area
    order by anc.depth, c.allocation_parent_id is null
    limit 1
  `);
  const owner = owners.rows[0] ?? {
    id: contest.root,
    place: area.id,
    depth: group == null ? 0 : chain.length,
    parts: 0,
  };
  const roots = group?.ids ?? [owner.id];
  const isFederal = owner.depth === 0 && owner.parts >= 3;
  const base = chain.map((entry) => entry.segment);
  let found: Array<{ id: string; k: number; scheme: string }> = [];
  let parts: Array<{ k: number; name: string; unit: string }> = [];
  let federal: string | null = null;
  if (isFederal) {
    const [kids, districts] = await Promise.all([
      children(owner.id, area, chain.length === 0, day),
      db.execute<{ id: string }>(sql`
        select district_area_id as id from contest
        where allocation_parent_id = ${owner.id}::uuid and district_area_id is not null
      `),
    ]);
    const owned = new Set(districts.rows.map((row) => row.id));
    const scheme =
      kids.find((kid) => owned.has(kid.id))?.scheme ?? kids[0]?.scheme;
    const shown = kids.filter((kid) => kid.scheme === scheme);
    const below = await db.execute<{
      id: string;
      k: number;
      scheme: string;
    }>(sql`
      select a.id, a.k, a.scheme_code as scheme
      from area_parent p
      join area a on a.id = p.area_id
      join contest c on c.allocation_parent_id = ${owner.id}::uuid and c.district_area_id = p.parent_id
      cross join lateral (
        select from result_set rs
        cross join lateral (
          select from result_rollup r
          where r.result_set_k = rs.k and r.area_k = a.k and r.measure = any(${COUNTED})
          limit 1
        ) r
        where rs.contest_id = c.id and rs.status = 'active'
        limit 1
      ) hit
      where p.parent_id = any(${sql.param(shown.map((kid) => kid.id))}::uuid[])
        and p.valid @> ${day}::date
    `);
    const tiers = Map.groupBy(below.rows, (row) => row.scheme);
    found = [
      ...shown,
      ...below.rows.filter(
        (row) =>
          row.scheme !== scheme &&
          (tiers.get(row.scheme)?.length ?? 0) >= shown.length,
      ),
    ];
    federal = scheme ?? null;
    parts = shown
      .filter((kid) => owned.has(kid.id))
      .map((kid) => ({
        k: kid.k,
        name: kid.name,
        unit: [...base, kid.segment].join("/"),
      }));
  } else if (group != null) {
    const listed = await db.execute<{
      id: string;
      k: number;
      scheme: string;
    }>(sql`
      with recursive up(id, origin, depth) as (
        select c.district_area_id, c.district_area_id, 0 from contest c where c.id = any(${sql.param(group.ids)}::uuid[])
        union all
        select p.parent_id, up.origin, up.depth + 1 from area_parent p join up on p.area_id = up.id
        where p.valid @> ${day}::date and up.depth < 6 and up.id <> ${area.id}::uuid
      )
      select distinct a.id, a.k, a.scheme_code as scheme
      from up join area a on a.id = up.id
      where up.id <> ${area.id}::uuid
        and up.origin in (select origin from up where id = ${area.id}::uuid)
    `);
    found = listed.rows;
  } else {
    const listed = await db.execute<{
      id: string;
      k: number;
      scheme: string;
    }>(sql`
      with recursive grp(id) as (
        select unnest(${sql.param(roots)}::uuid[])
        union all
        select c.id from contest c join grp on c.allocation_parent_id = grp.id
      ),
      sets as (
        select rs.k from grp cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs
        where rs.status = 'active'
      ),
      up(id, depth) as (
        select ${area.id}::uuid, 0
        union all
        select p.parent_id, up.depth + 1 from area_parent p join up on p.area_id = up.id where up.depth < 8
      ),
      down(id, depth) as (
        select ${area.id}::uuid, 0
        union all
        select p.area_id, down.depth + 1 from area_parent p join down on p.parent_id = down.id
        where p.valid @> ${day}::date and down.depth < 4 and ${owner.depth}::int > 0
      )
      select a.id, a.k, a.scheme_code as scheme
      from (
        select distinct r.area_k from sets
        cross join lateral (
          select x.area_k from result_rollup x where x.result_set_k = sets.k and x.measure = any(${COUNTED}) offset 0
        ) r
      ) r
      join area a on a.k = r.area_k
      where a.id not in (select id from up)
        and (${owner.depth}::int = 0 or a.id in (select id from down))
    `);
    found = listed.rows;
  }
  const counted = Map.groupBy(found, (row) => row.scheme);
  const schemes = await db.execute<{
    code: string;
    noun: string;
    rank: number;
    tiles: string | null;
  }>(sql`
    select s.code, s.noun, s.rank, (
      select coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) from boundary_set bs
      where bs.scheme_code = s.code
        and (bs.id = (select boundary_set_id from contest where id = ${contest.id}::uuid) or bs.valid @> ${day}::date)
      order by bs.id = (select boundary_set_id from contest where id = ${contest.id}::uuid) desc, bs.valid_from desc nulls last
      limit 1
    ) as tiles
    from scheme s
    where s.code = any(${sql.param([...counted.keys(), ""])}::text[])
      or s.code = (select scheme_code from area where id = ${area.id}::uuid)
  `);
  const own = await db.execute<{ scheme: string }>(sql`
    select scheme_code as scheme from area where id = ${area.id}::uuid
  `);
  const known = new Map(schemes.rows.map((row) => [row.code, row]));
  const levels = counted
    .entries()
    .filter(([, rows]) => rows.length > 1)
    .map(([code, rows]) => ({
      code,
      noun: known.get(code)?.noun ?? "",
      count: rows.length,
      tiles: stored(known.get(code)?.tiles),
      rank: known.get(code)?.rank ?? 0,
    }))
    .toArray()
    .toSorted((a, b) => a.count - b.count || a.rank - b.rank);
  const level =
    levels.find((entry) => entry.code === input.level) ??
    levels.find((entry) => entry.code === federal && entry.tiles != null) ??
    (input.level === "coarse"
      ? levels.find((entry) => entry.tiles != null)
      : undefined) ??
    levels.findLast((entry) => entry.tiles != null && entry.count <= 25_000) ??
    levels.findLast((entry) => entry.tiles != null) ??
    levels.find((entry) => entry.code === federal) ??
    levels.at(-1) ??
    null;
  const shown = level == null ? [] : (counted.get(level.code) ?? []);
  const [labels, slugs, frame, targets, relations] = await Promise.all([
    names(
      [
        contest.id,
        area.id,
        contest.place?.id ?? area.id,
        ...(group == null ? [] : [contest.election, group.owner]),
        ...(input.isLean === true ? [] : shown.map((row) => row.id)),
      ],
      contest.date,
    ),
    paths([group == null ? contest.id : contest.election]),
    input.isLean === true
      ? null
      : framing(
          area.id,
          day,
          shown.map((row) => row.k),
        ),
    group != null
      ? db.execute<{ id: string; date: string; named: string }>(sql`
          with recursive tops(top, id, date) as (
            select e.id, e.id, e.date_from from election e
            where e.polity_id = (select polity_id from election where id = ${contest.election}::uuid)
              and e.parent_id is not distinct from (select parent_id from election where id = ${contest.election}::uuid)
              and e.date_from < ${day}::date and e.status not in ('annulled', 'canceled')
            union all
            select tops.top, k.id, tops.date from election k join tops on k.parent_id = tops.id
          )
          select (array_agg(c.id order by c.term is not null, c.id))[1]::text as id, tops.date::text as date, tops.top as named
          from tops
          join contest c on c.election_id = tops.id
          join contest me on me.id = ${contest.id}::uuid
          where coalesce(c.body_id, c.office_id) = coalesce(me.body_id, me.office_id) and c.kind = me.kind
            and not exists (
              select from contest pr join tops t on t.id = pr.election_id and t.top = tops.top where pr.id = c.previous_round_id
            )
            and c.allocation_parent_id is null and not c.notional and c.district_area_id is not null
          group by tops.top, tops.date
          having count(*) * 2 >= ${group.ids.length}
          order by tops.date desc
          limit 16
        `)
      : db.execute<{ id: string; date: string; named: string }>(sql`
      select c.id, e.date_from::text as date, c.id as named
      from contest c
      join election e on e.id = c.election_id
      join contest me on me.id = ${contest.root}::uuid
        and c.office_id is not distinct from me.office_id and c.body_id is not distinct from me.body_id and c.kind = me.kind
      where c.allocation_parent_id is null and c.id <> ${contest.root}::uuid
        and e.polity_id = (select polity_id from election where id = ${contest.election}::uuid)
        and e.date_from < ${day}::date and e.status not in ('annulled', 'canceled')
        and (e.kind = 'indirect') = ${contest.indirect}
        and ${serial("c", "e")} = ${contest.series}
      order by e.date_from desc
      limit 16
    `),
    branches(day),
  ]);
  const wanted =
    input.vs == null || input.vs === "" || input.vs === "none"
      ? null
      : input.vs === "previous"
        ? (targets.rows[0] ?? null)
        : (targets.rows.find((row) => row.id === input.vs) ?? null);
  const counterpart =
    wanted == null
      ? null
      : group != null
        ? await db.execute<{
            id: string;
            date: string;
            ids: Array<string>;
          }>(sql`
            with recursive up(id, polity, parent) as (
              select e.id, e.polity_id, e.parent_id from contest w join election e on e.id = w.election_id
              where w.id = ${wanted.id}::uuid
              union all
              select e.id, e.polity_id, e.parent_id from election e join up on e.id = up.parent
            ),
            down(id) as (
              (select up.id from up
                where up.polity = (select polity_id from election where id = ${contest.election}::uuid)
                order by up.parent is null limit 1)
              union all
              select k.id from election k join down on k.parent_id = down.id
            )
            select ${wanted.id}::text as id, ${wanted.date}::text as date, array_agg(c.id)::text[] as ids
            from contest w
            join down on true
            join contest c on c.election_id = down.id
              and coalesce(c.body_id, c.office_id) = coalesce(w.body_id, w.office_id) and c.kind = w.kind
              and c.term is not distinct from w.term
              and not exists (select from contest pr join down d on d.id = pr.election_id where pr.id = c.previous_round_id)
              and c.allocation_parent_id is null and not c.notional and c.district_area_id is not null
            where w.id = ${wanted.id}::uuid
          `)
        : await db.execute<{
            id: string;
            date: string;
            ids?: Array<string>;
          }>(sql`
        with recursive grp(id) as (
          select ${wanted.id}::uuid
          union all
          select c.id from contest c join grp on c.allocation_parent_id = grp.id
        )
        select coalesce((
          select c.id from grp join contest c on c.id = grp.id
          join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          where coalesce(c.district_area_id, pt.area_id) = ${owner.place}::uuid
          limit 1
        ), ${wanted.id}::uuid)::text as id, ${wanted.date}::text as date
      `);
  const crosswalk = await db.execute<{
    from_k: number;
    to_k: number;
    weight: number;
  }>(sql`
    select w.from_k, w.to_k, w.weight::float8 as weight
    from crosswalk x
    join crosswalk_weight w on w.crosswalk_id = x.id
    where x.to_boundary_set_id = (
        select id from boundary_set
        where scheme_code = ${level?.code ?? null} and coalesce(tiles_key, scheme_code || '/' || vintage) = ${level?.tiles ?? null}
        limit 1
      )
      and x.from_boundary_set_id = (
        select id from boundary_set
        where scheme_code = ${level?.code ?? null} and valid @> ${wanted?.date ?? null}::date
        order by valid_from desc nulls last
        limit 1
      )
      and w.to_k = any(${sql.param(shown.map((row) => row.k))}::int[])
      and not exists (
        select from crosswalk y
        where y.from_boundary_set_id = x.from_boundary_set_id and y.to_boundary_set_id = x.to_boundary_set_id
          and y.method = 'population' and x.method <> 'population'
      )
  `);
  const spread = new Map<number, Array<[number, number]>>();
  for (const row of crosswalk.rows) {
    const list = spread.get(row.from_k) ?? [];
    list.push([row.to_k, row.weight]);
    spread.set(row.from_k, list);
  }
  const then = counterpart?.rows[0] ?? null;
  const districts = new Map(
    group == null
      ? []
      : (
          await db.execute<{ id: string; k: number }>(sql`
            select c.id, a.k from contest c join area a on a.id = c.district_area_id
            where c.id = any(${sql.param(group.ids)}::uuid[])
          `)
        ).rows.map((row) => [row.k, row.id]),
  );
  const [tabulated, at, moved] = await Promise.all([
    tabulate(
      roots,
      shown.map((row) => ({
        k: row.k,
        name: labels.full.get(row.id) ?? "",
        path: null,
        isContest: districts.has(row.k),
        owner: districts.get(row.k) ?? null,
        isApart: group != null,
      })),
      contest.date,
      then == null
        ? null
        : {
            ids: then.ids ?? [then.id],
            date: then.date,
            ...(spread.size > 0 && { spread }),
          },
      relations,
      "party",
      input.group,
    ),
    input.isLean === true
      ? new Map<number, [number, number]>()
      : anchors(
          shown.map((row) => row.k),
          day,
        ),
    redrawn(
      shown.filter(() => spread.size === 0).map((row) => row.id),
      contest,
      then,
      area.id,
    ),
  ]);
  const titles = await names(
    targets.rows.map((row) => row.named),
    null,
  );
  const strays = tabulated.legend.flatMap((item) =>
    group != null && item.key.startsWith("person:") && item.party != null
      ? [item.party]
      : [],
  );
  const parties =
    strays.length === 0 ? null : await names(strays, contest.date);
  const merged: typeof tabulated.legend = [];
  const joined = tabulated.legend.map((item) => {
    const party =
      parties == null || !item.key.startsWith("person:") ? null : item.party;
    const key =
      party == null
        ? item.key
        : (tabulated.legend.find(
            (other) =>
              !other.key.startsWith("person:") && other.party === party,
          )?.key ?? `party:${party}`);
    const at = merged.findIndex((other) => other.key === key);
    if (at >= 0) {
      return at;
    }
    merged.push(
      party == null
        ? item
        : {
            ...item,
            key,
            label: parties?.full.get(party) ?? item.label,
            short: parties?.short.get(party) ?? null,
            person: null,
          },
    );
    return merged.length - 1;
  });
  const weight = merged.map(() => 0);
  for (const row of tabulated.rows) {
    for (const [index, share] of row.shares.entries()) {
      const at = joined[index] ?? index;
      weight[at] = (weight[at] ?? 0) + share * row.valid;
    }
  }
  const order = merged
    .keys()
    .toArray()
    .toSorted((a, b) =>
      parties == null ? a - b : (weight[b] ?? 0) - (weight[a] ?? 0),
    );
  const legend = order.flatMap((index) => merged[index] ?? []);
  const fold = joined.map((index) => order.indexOf(index));
  const merge = (values: Array<number>) => {
    const summed = legend.map(() => 0);
    for (const [index, value] of values.entries()) {
      const at = fold[index] ?? index;
      summed[at] = (summed[at] ?? 0) + value;
    }
    return summed;
  };
  const rows = tabulated.rows.map((row) => ({
    ...row,
    shares: merge(row.shares),
    earlier: row.earlier == null ? null : merge(row.earlier),
    absent: row.absent?.flatMap((index) =>
      fold.every(
        (at, other) => at !== fold[index] || row.absent?.includes(other),
      )
        ? [fold[index] ?? index]
        : [],
    ),
    leader: row.leader == null ? null : (fold[row.leader] ?? row.leader),
    held:
      row.held?.map(([at, seats]): [number, number] => [
        fold[at] ?? at,
        seats,
      ]) ?? null,
    prior: row.prior == null ? null : (fold[row.prior] ?? row.prior),
    ...(moved.has(row.k) && { earlier: null, prior: null }),
  }));
  const tail = /, [^,]+$/u.exec(rows[0]?.name ?? "")?.[0];
  const isShared =
    tail != null &&
    rows.length > 1 &&
    rows.every((row) => row.name.endsWith(tail));
  const path = slugs.get(group == null ? contest.id : contest.election) ?? null;
  const statuses =
    level?.code === "world.country"
      ? new Map(
          (
            await db.execute<{ k: number; body: string }>(sql`
              select a.k, n.body_md as body from area a join note n on n.entity_id = a.id and n.kind = 'context'
              where a.k = any(${sql.param(rows.map((row) => row.k))}::int[])
            `)
          ).rows.map((row) => [row.k, row.body]),
        )
      : null;
  return {
    title:
      group == null
        ? (labels.full.get(contest.id) ?? "")
        : `${labels.full.get(contest.election) ?? ""}, ${labels.full.get(group.owner) ?? ""}`,
    scope: {
      name: labels.full.get(area.id) ?? "",
      k: area.k,
      unit: base.join("/"),
      path:
        path == null ? null : group == null ? [path, ...base].join("/") : path,
    },
    trail: [
      { name: labels.full.get(contest.place?.id ?? area.id) ?? "", unit: "" },
      ...chain.map((entry, index) => ({
        name: entry.name,
        unit: base.slice(0, index + 1).join("/"),
      })),
    ],
    date: contest.date,
    outline: stored(known.get(own.rows[0]?.scheme ?? "")?.tiles),
    frame,
    levels: levels.map(({ rank: _rank, ...entry }) => entry),
    level: level?.code ?? null,
    parts,
    targets: targets.rows.map((row) => ({
      id: row.id,
      label: titles.full.get(row.named) ?? "",
      date: row.date,
    })),
    target: wanted?.id ?? null,
    estimated: spread.size > 0,
    isPending: levels.some(
      (entry) => entry.tiles == null && known.get(entry.code)?.tiles != null,
    ),
    isAllied: tabulated.isAllied,
    legend: legend.map((item) => ({
      key: item.key,
      label:
        item.short != null &&
        legend.some((other) => other !== item && other.label === item.label)
          ? `${item.label} (${item.short})`
          : item.label,
      short: item.short,
      color: item.color,
    })),
    places: {
      k: rows.map((row) => row.k),
      name: rows.map((row) =>
        isShared ? row.name.slice(0, -tail.length) : row.name,
      ),
      valid: rows.map((row) => row.valid),
      x: rows.map((row) => {
        const point = at.get(row.k);
        return point == null ? null : Math.round(point[0] * 1e4) / 1e4;
      }),
      y: rows.map((row) => {
        const point = at.get(row.k);
        return point == null ? null : Math.round(point[1] * 1e4) / 1e4;
      }),
      leader: rows.map((row) => row.leader),
      held: rows.map((row) => row.held),
      prior: rows.map((row) => row.prior),
      shares: legend.map((_, index) =>
        rows.map((row) =>
          row.absent?.includes(index) === true
            ? null
            : Math.round((row.shares[index] ?? 0) * 10_000),
        ),
      ),
      before: legend.map((_, index) =>
        rows.map((row) =>
          row.earlier == null
            ? null
            : Math.round((row.earlier[index] ?? 0) * 10_000),
        ),
      ),
      redrawn: rows.map((row) => moved.get(row.k) ?? null),
      ...(statuses != null && {
        status: rows.map((row) => statuses.get(row.k) ?? null),
      }),
    },
  };
}

async function mosaic(input: { id: string; vs?: string; group: Grouping }) {
  const load = (
    unit: string | undefined,
    level: string | undefined,
    isLean = true,
  ) =>
    atlas({
      id: input.id,
      unit,
      level,
      vs: input.vs,
      group: input.group,
      isLean,
    });
  const rastered = (tiles: string | null) =>
    tiles != null &&
    existsSync(path.resolve("../../data/plane", `${tiles}.ids.pmtiles`));
  const base = await load(undefined, undefined);
  const middle = base.levels.findLast(
    (level) => level.code !== base.level && rastered(level.tiles),
  );
  const units =
    base.parts.length > 0 ? base.parts.map((part) => part.unit) : [""];
  const sets: Array<{
    set: Awaited<ReturnType<typeof atlas>>;
    part: number | null;
  }> = [];
  if (middle != null) {
    sets.push({ set: await load(undefined, middle.code, false), part: null });
  }
  const queue = [...units.entries()];
  const finer: Array<
    { set: Awaited<ReturnType<typeof atlas>>; part: number | null } | undefined
  > = [];
  await Promise.all(
    Array.from({ length: 4 }, async () => {
      for (let next = queue.shift(); next != null; next = queue.shift()) {
        const [at, unit] = next;
        const first = unit === "" ? base : await load(unit, undefined);
        const finest = first.levels.findLast(
          (level) => level.code !== middle?.code && rastered(level.tiles),
        );
        if (finest != null) {
          finer[at] = {
            set:
              finest.code === first.level
                ? first
                : await load(unit === "" ? undefined : unit, finest.code),
            part: unit === "" ? null : at,
          };
        }
      }
    }),
  );
  for (const set of finer) {
    if (set != null) {
      sets.push(set);
    }
  }
  const homes = new Map<number, number>();
  if (middle != null && base.parts.length > 0) {
    const found = await db.execute<{ k: number; part: number }>(sql`
      with recursive up(k, id, depth) as (
        select a.k, a.id, 0 from area a
        where a.k = any(${sql.param(sets[0]?.set.places.k ?? [])}::int[])
        union all
        select up.k, p.parent_id, up.depth + 1 from up
        join area_parent p on p.area_id = up.id and p.valid @> ${base.date ?? "infinity"}::date
        where up.depth < 6
      )
      select distinct on (up.k) up.k, a.k as part from up join area a on a.id = up.id
      where a.k = any(${sql.param(base.parts.map((part) => part.k))}::int[])
      order by up.k, up.depth
    `);
    const at = new Map(base.parts.map((part, index) => [part.k, index]));
    for (const row of found.rows) {
      const index = at.get(row.part);
      if (index != null) {
        homes.set(row.k, index);
      }
    }
  }
  const legend = [...base.legend];
  const layers: Array<{ code: string; noun: string; tiles: string }> = [];
  const rows: Array<{
    k: number;
    name: string;
    valid: number;
    part: number | null;
    layer: number;
    leader: number | null;
    held: Array<[number, number]> | null;
    prior: number | null;
    shares: Array<number | null>;
    before: Array<number | null>;
  }> = [];
  const seen = new Set<number>();
  for (const { set, part } of sets) {
    const level = set.levels.find((entry) => entry.code === set.level);
    if (
      level?.tiles != null &&
      !layers.some((layer) => layer.tiles === `${level.tiles}.ids`)
    ) {
      layers.push({
        code: level.code,
        noun: level.noun,
        tiles: `${level.tiles}.ids`,
      });
    }
    const layer = Math.max(
      0,
      layers.findIndex((entry) => entry.code === set.level),
    );
    const at = set.legend.map((item) => {
      const index = legend.findIndex((entry) => entry.key === item.key);
      if (index >= 0) {
        return index;
      }
      legend.push(item);
      return legend.length - 1;
    });
    for (const [i, k] of set.places.k.entries()) {
      if (seen.has(k)) {
        continue;
      }
      seen.add(k);
      const shares = legend.map(() => null as number | null);
      const before = legend.map(() => null as number | null);
      for (const [index, column] of set.places.shares.entries()) {
        const value = column[i];
        shares[at[index] ?? 0] = value == null ? null : Math.round(value / 10);
      }
      for (const [index, column] of set.places.before.entries()) {
        const value = column[i];
        before[at[index] ?? 0] = value == null ? null : Math.round(value / 10);
      }
      const leader = set.places.leader[i];
      const prior = set.places.prior[i];
      rows.push({
        k,
        name:
          middle != null && set === sets[0]?.set
            ? (set.places.name[i] ?? "")
            : "",
        valid: set.places.valid[i] ?? 0,
        part: part ?? homes.get(k) ?? null,
        layer,
        leader: leader == null ? null : (at[leader] ?? null),
        held:
          set.places.held?.[i]?.map(([index, seats]): [number, number] => [
            at[index] ?? index,
            seats,
          ]) ?? null,
        prior: prior == null ? null : (at[prior] ?? null),
        shares,
        before,
      });
    }
  }
  rows.sort((a, b) => a.k - b.k);
  const isCompared = rows.some((row) =>
    row.before.some((value) => value != null),
  );
  const placed = new Map<number, number>();
  for (const row of rows) {
    for (const [index] of row.shares
      .entries()
      .toArray()
      .toSorted(([, a], [, b]) => (b ?? -1) - (a ?? -1))
      .slice(0, 2)) {
      placed.set(index, (placed.get(index) ?? 0) + 1);
    }
  }
  const kept = new Set(
    placed
      .entries()
      .filter(([, total]) => total >= rows.length / 500)
      .map(([index]) => index),
  );
  const order = [
    ...legend.keys().filter((index) => kept.has(index)),
    ...legend.keys().filter((index) => !kept.has(index)),
  ];
  const moved = new Map(order.map((index, at) => [index, at]));
  return {
    target: base.target,
    legend: order.flatMap((index) => {
      const item = legend[index];
      return item == null ? [] : [item];
    }),
    layers,
    places: {
      k: rows.map((row, i) => row.k - (rows[i - 1]?.k ?? 0)),
      name: rows.map((row) => row.name),
      valid: rows.map((row) => row.valid),
      part: rows.map((row) => row.part),
      layer: rows.map((row) => row.layer),
      leader: rows.map((row) =>
        row.leader == null ? null : (moved.get(row.leader) ?? null),
      ),
      held: rows.map(
        (row) =>
          row.held?.map(([index, seats]): [number, number] => [
            moved.get(index) ?? index,
            seats,
          ]) ?? null,
      ),
      prior: isCompared
        ? rows.map((row) =>
            row.prior == null ? null : (moved.get(row.prior) ?? null),
          )
        : [],
      shares: order
        .filter((index) => kept.has(index))
        .map((index) => rows.map((row) => row.shares[index] ?? null)),
      before: isCompared
        ? order
            .filter((index) => kept.has(index))
            .map((index) => rows.map((row) => row.before[index] ?? null))
        : [],
    },
  };
}

async function lean(input: {
  id: string;
  unit: string;
  group?: "option" | "alliance";
}) {
  const contest = await find(input.id);
  if (contest.indirect) {
    throw new ORPCError("NOT_FOUND");
  }
  const chain = await descend(contest, input.unit);
  const place = chain.at(-1);
  if (place == null) {
    throw new ORPCError("NOT_FOUND");
  }
  const above = chain
    .slice(0, -1)
    .map((entry) => entry.segment)
    .join("/");
  const measure = async (group: "option" | "alliance") => {
    const near = await atlas({
      id: input.id,
      unit: above === "" ? undefined : above,
      level: place.scheme,
      vs: "previous",
      group,
      isLean: true,
    });
    const whole =
      chain.length === 1
        ? near
        : await atlas({ id: input.id, group, isLean: true });
    const axis = whole.legend.slice(0, 2);
    const spread = (
      set: typeof near,
      column: "shares" | "before",
      i: number,
    ) => {
      const [a, b] = axis.map((item) => {
        const index = set.legend.findIndex((entry) => entry.key === item.key);
        return index < 0 ? null : set.places[column][index]?.[i];
      });
      return a == null || b == null ? null : (a - b) / 10_000;
    };
    const held = near.places.k
      .keys()
      .filter((i) => near.places.leader[i] != null)
      .toArray();
    const counted = held.filter((i) => spread(near, "shares", i) != null);
    const at = near.places.k.indexOf(place.k);
    return {
      group,
      near,
      whole,
      axis,
      spread,
      at,
      held,
      counted,
      margin: axis.length < 2 || at < 0 ? null : spread(near, "shares", at),
    };
  };
  const asked = await measure(input.group ?? "option");
  const chosen =
    asked.group === "option" &&
    (asked.near.isAllied || asked.whole.isAllied) &&
    (asked.margin == null || asked.counted.length < asked.held.length)
      ? await measure("alliance").then((allied) =>
          allied.margin != null &&
          (asked.margin == null || allied.counted.length > asked.counted.length)
            ? allied
            : asked,
        )
      : asked;
  const { group, near, whole, axis, spread, at, held, counted, margin } =
    chosen;
  if (margin == null) {
    throw new ORPCError("NOT_FOUND");
  }
  const round = (value: number) => Math.round(value * 10_000) / 10_000;
  const overall = (set: typeof near) => {
    let [weighted, total] = [0, 0];
    for (const [i, valid] of set.places.valid.entries()) {
      const value = spread(set, "shares", i);
      if (value != null && set.places.leader[i] != null) {
        weighted += value * valid;
        total += valid;
      }
    }
    return total === 0 ? 0 : round(weighted / total);
  };
  const areas = await db.execute<{ id: string; k: number }>(sql`
    select id, k from area where k = any(${sql.param(counted.map((i) => near.places.k[i] ?? -1))}::int[])
  `);
  const labels = await names(
    areas.rows.map((row) => row.id),
    contest.date,
  );
  const named = new Map(
    areas.rows.map((row) => [row.k, labels.full.get(row.id) ?? ""]),
  );
  const places = counted.map((i) => ({
    name: named.get(near.places.k[i] ?? -1) ?? "",
    margin: round(spread(near, "shares", i) ?? 0),
    isOwn: i === at,
  }));
  const before = spread(near, "before", at);
  const previous = near.targets.find((target) => target.id === near.target);
  return {
    group,
    axis: axis.map((item) => ({
      key: item.key,
      label: item.label,
      color: item.color,
    })),
    place: {
      name: place.name,
      margin: round(margin),
      before: before == null ? null : round(before),
    },
    parent:
      chain.length === 1
        ? null
        : { name: chain.at(-2)?.name ?? "", margin: overall(near) },
    whole: { name: whole.trail[0]?.name ?? "", margin: overall(whole) },
    siblings: {
      noun: place.noun,
      count: places.length,
      total: held.length,
      rank:
        1 +
        places.filter((entry) =>
          margin >= 0
            ? entry.margin > round(margin)
            : entry.margin < round(margin),
        ).length,
      places: places.toSorted((a, b) => a.margin - b.margin),
    },
    previous:
      previous == null ? null : { label: previous.label, date: previous.date },
  };
}

export async function rebuildMosaics(only?: Array<string>) {
  const started = Date.now();
  const folder = path.resolve("../../data/mosaics");
  await mkdir(folder, { recursive: true });
  const listed =
    only ??
    (
      await db.execute<{ id: string }>(sql`
      select c.id from contest c
      where c.allocation_parent_id is null
        and exists (select from contest k where k.allocation_parent_id = c.id offset 2)
    `)
    ).rows.map((row) => row.id);
  const ids = new Set(listed);
  if (only == null) {
    for (const name of await readdir(folder)) {
      if (name.endsWith(".json") && !name.endsWith(".vs.json")) {
        ids.add(name.slice(0, -5));
      }
    }
  }
  const rasters: Array<string> = [];
  for (const name of await readdir(path.resolve("../../data/plane"), {
    recursive: true,
  })) {
    if (name.endsWith(".ids.pmtiles")) {
      const info = await stat(path.resolve("../../data/plane", name));
      rasters.push(`${name}@${info.mtimeMs}`);
    }
  }
  const counts = { built: 0, kept: 0, dropped: 0 };
  for (const id of ids) {
    const file = path.join(folder, `${id}.json`);
    const twin = path.join(folder, `${id}.vs.json`);
    const found = await db.execute<{ stamp: string }>(sql`
      with recursive grp(id) as (
        select ${id}::uuid
        union all
        select c.id from contest c join grp on c.allocation_parent_id = grp.id
      )
      select md5(concat_ws('|', 'parts-noun', string_agg(concat_ws(':', rs.k, rs.changeset_id, rs.status, rs.is_default), ',' order by rs.k),
        ${rasters.toSorted().join(",")}::text)) as stamp
      from grp join result_set rs on rs.contest_id = grp.id
    `);
    const mark = found.rows[0]?.stamp ?? "";
    const held = existsSync(file)
      ? (JSON.parse(await readFile(file, "utf8")) as { stamp: string })
      : null;
    const age = held == null ? 0 : Date.now() - (await stat(file)).mtimeMs;
    if (held?.stamp === mark && age < 86_400_000) {
      counts.kept += 1;
      continue;
    }
    try {
      const sheet = await mosaic({ id, group: "option" });
      await writeFile(`${file}.part`, JSON.stringify({ stamp: mark, sheet }));
      await rename(`${file}.part`, file);
      const compared = await mosaic({ id, vs: "previous", group: "option" });
      if (compared.target == null) {
        await rm(twin, { force: true });
      } else {
        const slug = await db.execute<{ path: string }>(sql`
          select path from slug where entity_id = ${compared.target}::uuid and is_current limit 1
        `);
        await writeFile(
          `${twin}.part`,
          JSON.stringify({
            stamp: mark,
            path: slug.rows[0]?.path ?? null,
            sheet: compared,
          }),
        );
        await rename(`${twin}.part`, twin);
      }
      counts.built += 1;
    } catch (error) {
      if (!(error instanceof ORPCError) || error.code !== "NOT_FOUND") {
        throw error;
      }
      await rm(file, { force: true });
      await rm(twin, { force: true });
      counts.dropped += 1;
    }
  }
  return `mosaics: ${counts.built} built, ${counts.kept} unchanged, ${counts.dropped} dropped in ${Math.round((Date.now() - started) / 1000)} s`;
}

export const contests = {
  trace: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/trace",
        summary:
          "Find the printed page regions a contest's numbers were read from",
      }),
    )
    .input(
      v.object({
        path: v.string(),
        key: v.nullable(v.string()),
        area: v.nullable(v.number()),
        place: v.optional(v.nullable(v.string())),
      }),
    )
    .output(
      v.object({
        count: v.number(),
        pages: v.array(
          v.object({
            image: v.string(),
            page: v.number(),
            width: v.number(),
            height: v.number(),
            box: v.tuple([v.number(), v.number(), v.number(), v.number()]),
            isExact: v.boolean(),
            publisher: v.string(),
            title: v.string(),
            url: v.nullable(v.string()),
          }),
        ),
        trail: v.array(
          v.object({
            publisher: v.string(),
            title: v.string(),
            url: v.nullable(v.string()),
            retrieved: v.nullable(v.string()),
            stage: v.string(),
            sets: v.number(),
            changeset: v.string(),
            changesets: v.number(),
            pipeline: v.nullable(v.string()),
            published: v.nullable(v.string()),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const area =
        input.area ??
        (input.place == null
          ? null
          : ((
              await db.execute<{ k: number }>(sql`
                select a.k from slug s
                join polity_territory pt on pt.polity_id = s.entity_id
                join area a on a.id = pt.area_id
                where s.path = ${input.place}
                order by upper_inf(pt.valid) desc, upper(pt.valid) desc limit 1
              `)
            ).rows[0]?.k ?? null));
      const prefixes = input.path
        .split("/")
        .map((_, index, parts) => parts.slice(0, index + 1).join("/"));
      const trail = db.execute<{
        publisher: string;
        title: string;
        url: string | null;
        retrieved: string | null;
        stage: string;
        sets: number;
        changeset: string;
        changesets: number;
        pipeline: string | null;
        published: string | null;
      }>(sql`
        with recursive head as (
          select s.entity_id as id from slug s join entity e on e.id = s.entity_id
          where s.path = any(${sql.param(prefixes)}::text[]) and e.type = 'contest'
          order by length(s.path) desc, s.is_current desc limit 1
        ),
        tree(id) as (
          select id from head
          union all
          select c.id from contest c join tree on c.allocation_parent_id = tree.id
        ),
        every as (
          select rs.k, rs.source_id, rs.stage, rs.changeset_id, rs.is_default, rs.completeness, ${latest("rs")} as is_latest
          from tree cross join lateral (select * from result_set x where x.contest_id = tree.id and x.status <> 'withdrawn' offset 0) rs
        ),
        target as (
          select ${area}::int as k where ${area}::int is not null
        ),
        picked as (
          select every.* from every cross join target
          where exists (select from result_rollup_unit u where u.result_set_k = every.k and u.area_k = target.k)
            or exists (select from result_rollup_sum r where r.result_set_k = every.k and r.area_k = target.k)
          order by
            exists (select from source s where s.id = every.source_id and s.license ilike '%ICPSR%'),
            (exists (select from source s where s.id = every.source_id and s.kind = 'official_body')
              and exists (select from result_rollup_unit u where u.result_set_k = every.k and u.area_k = target.k)) desc,
            every.is_default desc, array_position(${STAGES}, every.stage), every.completeness desc nulls last
          limit 1
        ),
        chosen as (
          select * from picked
          union all
          select * from every where is_latest and not exists (select from picked)
        )
        select s.publisher, s.title, s.url, chosen.stage::text as stage, count(*)::int as sets,
          (select max(a.retrieved_at)::date::text from source_artifact a where a.source_id = s.id) as retrieved,
          (array_agg(c.id::text order by c.published_at desc nulls last))[1] as changeset,
          count(distinct c.id)::int as changesets,
          (array_agg(c.pipeline order by c.published_at desc nulls last))[1] as pipeline,
          max(c.published_at)::date::text as published
        from chosen join source s on s.id = chosen.source_id join changeset c on c.id = chosen.changeset_id
        group by s.id, chosen.stage
        order by count(*) desc, s.publisher
      `);
      const pending = db.execute<{
        total: number;
        image: string;
        page: number;
        width: number;
        height: number;
        x0: number;
        y0: number;
        x1: number;
        y1: number;
        exact: boolean;
        publisher: string;
        title: string;
        url: string | null;
      }>(sql`
        with recursive head as (
          select s.entity_id as id from slug s join entity e on e.id = s.entity_id
          where s.path = any(${sql.param(prefixes)}::text[]) and e.type = 'contest'
          order by length(s.path) desc, s.is_current desc limit 1
        ),
        tree(id) as (
          select id from head
          union all
          select c.id from contest c join tree on c.allocation_parent_id = tree.id
        ),
        top as (
          select a.k from head join contest c on c.id = head.id join area a on a.id = c.district_area_id
        ),
        sets as (
          select rs.k, rs.source_id from tree cross join lateral (select * from result_set x where x.contest_id = tree.id and x.status <> 'withdrawn' offset 0) rs where ${latest("rs")}
        ),
        options as (
          select o.k from contest_option o join tree on tree.id = o.contest_id
          left join candidacy cy on cy.option_id = o.id
          left join entity_merge pm on pm.entity_id = o.party_id
          where ${input.key}::text is null
            or 'party:' || coalesce(pm.into_id, o.party_id)::text = ${input.key}
            or 'person:' || cy.person_id::text = ${input.key}
        ),
        hits as (
          select vl.*, ru.area_relation = 'equals' and ua.k is not distinct from coalesce(${area}::int, (select k from top)) as exact,
            sets.source_id as set_source
          from value_locator vl join sets on sets.k = vl.result_set_k
          join reporting_unit ru on ru.k = vl.unit_k
          left join area ua on ua.id = ru.area_id
          where (vl.option_k in (select k from options) or (${input.key}::text is null and vl.option_k = 0))
            and (${area}::int is null or ua.k = ${area}::int)
        )
        select (select count(distinct unit_k) from hits)::int as total, p.image_key as image, p.page_number as page, p.width, p.height,
          h.x0, h.y0, h.x1, h.y1, h.exact, s.publisher, s.title, s.url
        from hits h join source_page p on p.id = h.page_id
        join source s on s.id = coalesce(h.source_id, h.set_source)
        order by h.exact desc, h.option_k = 0 desc, h.measure, h.count_round, p.page_number
        limit 3
      `);
      const [listed, found] = await Promise.all([trail, pending]);
      return {
        count: found.rows[0]?.total ?? 0,
        trail: listed.rows,
        pages: found.rows.map((row) => ({
          image: row.image.replace(/^pages\//, ""),
          page: row.page,
          width: row.width,
          height: row.height,
          box: [row.x0, row.y0, row.x1, row.y1] as [
            number,
            number,
            number,
            number,
          ],
          isExact: row.exact,
          publisher: row.publisher,
          title: row.title,
          url: row.url,
        })),
      };
    }),
  locate: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/locate",
        summary: "Find the contest a site path names",
      }),
    )
    .input(v.object({ path: v.string() }))
    .output(v.object({ id: v.string() }))
    .handler(async ({ input }) => {
      const found = await db.execute<{ id: string }>(sql`
        select s.entity_id as id from slug s join entity e on e.id = s.entity_id
        where s.path = ${input.path} and e.type = 'contest'
        order by s.is_current desc limit 1
      `);
      const row = found.rows[0];
      if (row == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return row;
    }),
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}",
        summary:
          "Get a contest's results at a place, by the places within it, compared with another election",
      }),
    )
    .input(Input)
    .output(
      v.object({
        name: v.string(),
        kind: v.string(),
        path: v.nullable(v.string()),
        breadcrumbs: v.array(Link),
        election: v.object({
          name: v.string(),
          path: v.nullable(v.string()),
          date: v.nullable(v.string()),
          year: v.nullable(v.number()),
          status: v.string(),
        }),
        area: v.string(),
        scope: v.object({
          name: v.string(),
          noun: v.nullable(v.string()),
          isFine: v.boolean(),
          parent: v.nullable(v.string()),
          home: v.nullable(v.string()),
          abroad: v.nullable(v.object({ electorate: v.nullable(v.string()) })),
          trail: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              query: v.optional(v.string()),
              siblings: v.nullable(
                v.object({
                  contest: v.string(),
                  unit: v.nullable(v.string()),
                  level: v.string(),
                  noun: v.string(),
                }),
              ),
            }),
          ),
        }),
        series: v.array(
          v.object({
            date: v.nullable(v.string()),
            year: v.nullable(v.number()),
            path: v.nullable(v.string()),
            name: v.string(),
            current: v.boolean(),
            annulled: v.boolean(),
            isRemainder: v.boolean(),
            notional: v.boolean(),
            gap: v.boolean(),
            winner: v.nullable(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                votes: v.number(),
                share: v.number(),
                total: v.number(),
                measure: v.string(),
                seats: v.optional(
                  v.array(
                    v.object({
                      key: v.string(),
                      label: v.string(),
                      color: v.nullable(v.string()),
                    }),
                  ),
                ),
              }),
            ),
          }),
        ),
        notes: v.array(v.object({ kind: v.string(), body: v.string() })),
        wording: v.array(
          v.object({
            kind: v.picklist(["text", "summary", "link"]),
            body: v.nullable(v.string()),
            url: v.nullable(v.string()),
            publisher: v.string(),
          }),
        ),
        children: v.array(Link),
        places: Places,
        sets: v.array(ResultSet),
        citations: v.array(
          v.object({
            publisher: v.string(),
            title: v.string(),
            url: v.nullable(v.string()),
          }),
        ),
        checks: v.array(
          v.object({
            code: v.string(),
            units: v.array(
              v.object({
                name: v.string(),
                details: v.record(v.string(), v.number()),
              }),
            ),
          }),
        ),
        declared: v.nullable(
          v.object({
            kind: v.string(),
            rows: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                person: v.nullable(v.string()),
                party: v.nullable(v.string()),
                outcome: v.string(),
                note: v.nullable(v.string()),
                isDeclined: v.boolean(),
                seats: v.nullable(v.number()),
              }),
            ),
          }),
        ),
        incumbency: v.nullable(v.picklist(["defeated", "open"])),
        last: v.optional(
          v.nullable(
            v.object({
              date: v.nullable(v.string()),
              path: v.nullable(v.string()),
              rows: v.array(
                v.object({
                  key: v.string(),
                  label: v.string(),
                  person: v.nullable(v.string()),
                  party: v.nullable(v.string()),
                  color: v.nullable(v.string()),
                  share: v.number(),
                }),
              ),
            }),
          ),
        ),
        slate: v.nullable(
          v.object({
            state: v.picklist(["scheduled", "counting", "unreported"]),
            isUnqualified: v.boolean(),
            options: v.array(
              v.object({
                key: v.string(),
                kind: v.string(),
                label: v.string(),
                party: v.nullable(
                  v.object({
                    id: v.string(),
                    name: v.string(),
                    short: v.nullable(v.string()),
                    color: v.nullable(v.string()),
                  }),
                ),
                person: v.nullable(v.string()),
                isIncumbent: v.boolean(),
                isIndependent: v.boolean(),
              }),
            ),
          }),
        ),
        quorum: v.nullable(v.number()),
        ballot: v.optional(v.nullable(v.number())),
        isStv: v.optional(v.boolean()),
        logos: LogoMap,
        explanations: v.array(
          v.object({
            key: v.string(),
            kind: v.picklist([
              "note",
              "access",
              "remainder",
              "compensation",
              "threshold",
              "pooling",
              "bonus",
              "independent",
            ]),
            label: v.string(),
            note: v.nullable(v.string()),
            percent: v.nullable(v.number()),
            votes: v.nullable(v.number()),
            beaten: v.array(v.string()),
            person: v.nullable(v.string()),
            joined: v.nullable(v.string()),
            system: v.nullable(v.string()),
            rule: v.nullable(v.string()),
          }),
        ),
        channels: v.optional(
          v.array(
            v.object({
              code: v.picklist(CHANNELS),
              votes: v.number(),
              leader: v.nullable(
                v.object({
                  label: v.string(),
                  color: v.nullable(v.string()),
                  share: v.number(),
                }),
              ),
            }),
          ),
        ),
        partial: v.nullable(v.object({ held: v.number(), of: v.number() })),
        turnout: v.nullable(
          v.object({
            registered: v.number(),
            voters: v.number(),
            reporting: v.optional(v.number()),
            of: v.optional(v.number()),
            basis: v.optional(v.nullable(v.picklist(["valid", "rejected"]))),
            earlier: v.optional(
              v.nullable(
                v.object({ registered: v.number(), voters: v.number() }),
              ),
            ),
          }),
        ),
        count: v.nullable(
          v.object({
            round: v.number(),
            last: v.number(),
            rows: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                person: v.nullable(v.string()),
                party: v.nullable(v.string()),
                outcome: v.nullable(v.string()),
                votes: v.array(v.nullable(v.number())),
              }),
            ),
            exhausted: v.array(v.nullable(v.number())),
            initial: v.number(),
            isSupplementary: v.boolean(),
            stv: v.nullable(
              v.object({
                seats: v.number(),
                quota: v.number(),
                isDerived: v.boolean(),
                steps: v.array(
                  v.object({
                    kind: v.picklist(["first", "surplus", "exclusion", "none"]),
                    from: v.array(v.string()),
                    elected: v.array(v.string()),
                  }),
                ),
              }),
            ),
          }),
        ),
        isDerived: v.boolean(),
        current: v.nullable(
          v.object({
            valid: v.number(),
            measure: v.string(),
            complete: v.boolean(),
            sets: v.array(ResultSet),
            isAllied: v.boolean(),
            isTally: v.boolean(),
          }),
        ),
        electors: v.nullable(
          v.object({
            rows: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                person: v.nullable(v.string()),
                party: v.nullable(v.string()),
                lean: v.nullable(v.number()),
                votes: v.number(),
                popular: v.nullable(v.number()),
              }),
            ),
            appointed: v.nullable(v.number()),
            link: v.object({
              path: v.nullable(v.string()),
              date: v.nullable(v.string()),
              popular: v.boolean(),
            }),
          }),
        ),
        chamber: v.nullable(
          v.object({
            link: v.nullable(Link),
            size: v.nullable(v.number()),
            places: v.array(Link),
            here: v.nullable(v.number()),
            parties: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                lean: v.nullable(v.number()),
                seats: v.number(),
                here: v.number(),
                members: v.array(
                  v.object({
                    place: v.number(),
                    name: v.nullable(v.string()),
                    position: v.nullable(v.number()),
                    person: v.nullable(v.string()),
                    party: v.nullable(v.string()),
                    affiliation: v.nullable(v.string()),
                  }),
                ),
              }),
            ),
          }),
        ),
        partners: v.array(v.array(v.string())),
        target: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            year: v.nullable(v.number()),
            status: v.string(),
            valid: v.number(),
            complete: v.boolean(),
            sets: v.array(ResultSet),
          }),
        ),
        comparison: v.object({
          label: v.nullable(v.picklist(["exact", "estimated"])),
          notes: v.array(
            v.object({
              code: v.picklist([
                "no_target",
                "not_covered",
                "different_race",
                "annulled",
                "partial",
                "alternative",
                "redrawn",
                "channels",
              ]),
              side: v.nullable(v.picklist(["current", "target"])),
              value: v.nullable(v.string()),
            }),
          ),
        }),
        rows: v.array(
          v.object({
            key: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
            lean: v.nullable(v.number()),
            current: Cell,
            earlier: Cell,
            delta: v.nullable(v.number()),
            backed: v.nullable(v.string()),
            origin: v.nullable(v.string()),
            photo: Photo,
            nominated: v.array(
              v.object({ party: v.string(), label: v.string() }),
            ),
            supported: v.array(
              v.object({ party: v.string(), label: v.string() }),
            ),
            ballot: v.optional(
              v.nullable(
                v.object({ name: v.string(), path: v.nullable(v.string()) }),
              ),
            ),
          }),
        ),
        targets: v.array(
          v.object({
            value: v.string(),
            label: v.string(),
            detail: v.nullable(v.string()),
            year: v.nullable(v.number()),
            series: v.boolean(),
            annulled: v.boolean(),
            previous: v.boolean(),
          }),
        ),
        locator: v.optional(Places),
        deeper: v.optional(v.nullable(v.string())),
        county: v.optional(
          v.nullable(
            v.object({ name: v.string(), path: v.nullable(v.string()) }),
          ),
        ),
        origins: v.optional(
          v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              share: v.number(),
              first: v.nullable(v.number()),
              year: v.nullable(v.number()),
            }),
          ),
        ),
        overall: v.optional(
          v.nullable(
            v.object({
              place: v.string(),
              winners: v.array(
                v.object({
                  person: v.nullable(v.string()),
                  party: v.nullable(v.string()),
                  answer: v.nullable(v.string()),
                }),
              ),
            }),
          ),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const scoped = chain.at(-1);
      const isRoot = scoped == null;
      const area = scoped ?? contest.place;
      const unit = chain.map((entry) => entry.segment);
      const [
        parts,
        ancestors,
        cited,
        home,
        roll,
        reach,
        counted,
        split,
        explanations,
      ] = await Promise.all([
        db.execute<{ id: string }>(sql`
            select id from contest where allocation_parent_id = ${contest.id}::uuid
          `),
        db.execute<{
          id: string;
          area: string | null;
          scheme: string | null;
          noun: string | null;
          kind: string;
        }>(sql`
            with recursive up(id, parent, depth) as (
              select id, allocation_parent_id, 0 from contest where id = ${contest.id}::uuid
              union all
              select c.id, c.allocation_parent_id, up.depth + 1 from contest c join up on c.id = up.parent
            )
            select up.id, a.id as area, a.scheme_code as scheme, sc.noun, c.kind::text as kind
            from up
            join contest c on c.id = up.id
            join election e on e.id = c.election_id
            left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
            left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
            left join scheme sc on sc.code = a.scheme_code
            order by up.depth desc
          `),
        db.execute<{
          publisher: string;
          title: string;
          url: string | null;
        }>(sql`
            select distinct s.publisher, s.title, s.url
            from source s
            where s.id in (
              select ct.source_id from citation ct
              where ct.entity_id = ${contest.id}::uuid or ct.entity_id in (
                select cy.id from candidacy cy join contest_option o on o.id = cy.option_id
                where o.contest_id = ${contest.id}::uuid
              )
            ) or s.id in (
              select l.source_id from label l where l.entity_id = ${contest.id}::uuid and l.kind = 'ballot'
            )
            order by s.publisher, s.title
          `),
        db.execute<{ path: string }>(sql`
            select s.path from polity_territory t join slug s on s.entity_id = t.polity_id and s.is_current
            where t.area_id = ${area?.id}::uuid and t.polity_id <> ${contest.polity}::uuid
            order by upper(t.valid) desc nulls first limit 1
          `),
        poll(contest.id, area?.k ?? null),
        db.execute<{ held: number; of: number }>(sql`
            select count(*) filter (
                where exists (select from result_rollup x where x.result_set_k = rs.k and x.area_k = a.k)
              )::int as held, count(*)::int as of
            from contest c
            join election e on e.id = c.election_id
            join result_set rs on rs.contest_id = c.id and ${latest("rs")}
            left join boundary_set bs on bs.id = c.boundary_set_id
            join district_county dc on dc.district_area_id = c.district_area_id
              and dc.district_valid @> coalesce(bs.valid_from, e.date_from) and dc.county_valid @> e.date_from and dc.share >= 0.005
            join area a on a.id = dc.county_area_id
            where c.id = ${contest.id}::uuid and c.district_area_id = ${area?.id ?? null}::uuid
              and exists (
                select from result_rollup_sum x where x.result_set_k = rs.k and x.area_k = ${area?.k ?? null}::int and not x.complete
              )
          `),
        db.execute<{
          last: number;
          round: number | null;
          derived: boolean;
          seats: number | null;
          isShallow: boolean;
        }>(sql`
            select coalesce(t.last, 1) as last,
              t.last > 1 and (
                select 2 * count(distinct x.area_k) filter (where x.count_round = t.last) < count(distinct x.area_k) filter (where x.count_round = 1)
                from result_set rs cross join lateral (
                  select x.area_k, x.count_round from result_rollup x
                  where x.result_set_k = rs.k and x.count_round in (1, t.last) and x.measure = any(${COUNTED}) offset 0
                ) x
                where rs.contest_id = ${contest.id}::uuid and rs.status = 'active'
              ) as "isShallow",
              case when t.last > 1 then least(greatest(coalesce(nullif(${input.round}::int, 0), case when stv.seats is null then t.last else 1 end), 1), t.last) end as round,
              stv.seats,
              exists (
                select from result_set rs join note n on n.entity_id = rs.id and n.kind = 'methodology'
                where rs.contest_id = ${contest.id}::uuid and not rs.is_default
                  and exists (
                    select from result_rollup_unit u
                    where u.result_set_k = rs.k and not exists (
                      select from result_set d join result_rollup_unit w on w.result_set_k = d.k and w.area_k = u.area_k
                      where d.contest_id = ${contest.id}::uuid and ${latest("d")}
                    )
                  )
              ) as derived
            from (
              select max(r.count_round)::int as last from result_set rs cross join lateral (
                select x.count_round from result_rollup x
                where x.result_set_k = rs.k and x.area_k = ${contest.place?.k ?? null}::int and x.measure = any(${COUNTED}) offset 0
              ) r
              where rs.contest_id = ${contest.id}::uuid and ${latest("rs")}
            ) t
            left join lateral (
              select c.magnitude as seats from contest c
              left join electoral_system es on es.id = c.electoral_system_id
              where c.id = ${contest.id}::uuid and c.magnitude > 1
                and coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula') = 'stv'
            ) stv on true
          `),
        db.execute<{
          channel: number;
          option: number;
          person: string | null;
          party: string | null;
          value: number;
        }>(sql`
            with facts as (
              select r.channel, r.option_k, r.measure, r.count_round, r.value
              from result_set rs cross join lateral (
                select x.channel, x.option_k, x.measure, x.count_round, x.value from result_rollup x
                where x.result_set_k = rs.k and x.area_k = ${area?.k ?? null}::int and x.measure = any(${COUNTED}) and x.option_k <> 0 offset 0
              ) r
              where rs.contest_id = ${contest.id}::uuid and ${latest("rs")}
            ),
            first as (
              select measure, count_round from facts
              order by array_position(${COUNTED}, measure), count_round limit 1
            )
            select f.channel, f.option_k as option,
              (select cy.person_id from candidacy cy where o.kind = 'candidate' and cy.option_id = o.id order by cy.role <> 'head' limit 1) as person,
              o.party_id as party, sum(f.value)::float8 as value
            from facts f join first using (measure, count_round)
            join contest_option o on o.k = f.option_k
            group by 1, 2, 3, 4
            having (select count(distinct channel) from facts) > 1
          `),
        isRoot ? explain(contest, contest.place?.k ?? null) : [],
      ]);
      const [
        { last, round, derived, seats: elects, isShallow } = {
          last: 1,
          round: null,
          derived: false,
          seats: null,
          isShallow: false,
        },
      ] = counted.rows;
      const childIds = parts.rows.map((row) => row.id);
      const ancestorIds = ancestors.rows.map((row) => row.id);
      const [labels, slugs, crumbs, notes, sets, declared, wording] =
        await Promise.all([
          names(
            [
              contest.id,
              contest.election,
              ...childIds,
              ...ancestors.rows
                .map((row) => row.area)
                .filter((id) => id != null),
            ],
            contest.date,
          ),
          paths([contest.id, contest.election, ...childIds, ...ancestorIds]),
          breadcrumbs(contest.polity, contest.election),
          db.execute<{
            kind: string;
            body: string;
            set: number | null;
            publisher: string | null;
          }>(sql`
            select n.kind::text, n.body_md as body, rs.k as set, s.publisher
            from note n
            left join result_set rs on rs.id = n.entity_id
            left join source s on s.id = rs.source_id
            where exists (select from (
              select ${contest.id}::uuid as id, null::note_kind as kind
              union all
              select ${contest.election}::uuid, null
              union all
              select c.district_area_id, 'methodology' from contest c where c.id = ${contest.id}::uuid
              union all
              select rs.id, null from result_set rs
                where rs.contest_id = ${contest.id}::uuid and (${latest("rs")} or exists (
                  select from result_rollup r
                  where r.result_set_k = rs.k and r.measure = any(${COUNTED}) and not exists (
                    select from result_set d
                    where d.contest_id = ${contest.id}::uuid and ${latest("d")} and exists (
                      select from result_rollup q where q.result_set_k = d.k and q.area_k = r.area_k offset 0
                    )
                  )
                  offset 0
                ))
              union all
              select cy.id, null from candidacy cy join contest_option o on o.id = cy.option_id
              where o.contest_id = ${contest.id}::uuid
            ) w where w.id = n.entity_id and (w.kind is null or w.kind = n.kind))
            order by array_position(array['caveat','context','methodology','editorial']::note_kind[], n.kind)
          `),
          db.execute<{
            stage: string;
            status: string;
            completeness: number | null;
            is_default: boolean;
            kind: string;
            publisher: string;
            title: string;
            url: string | null;
            license: string | null;
            attribution: string | null;
            unreconciled: v.InferOutput<typeof Unreconciled> | null;
          }>(sql`
            select rs.stage::text, rs.status::text, rs.completeness::float8 as completeness, rs.is_default,
              s.kind::text, s.publisher, s.title, s.url, s.license, s.attribution,
              (select f.details from flag f where f.result_set_id = rs.id and f.check_code = 'reconcile.official' limit 1) as unreconciled
            from result_set rs join source s on s.id = rs.source_id
            where rs.contest_id = ${contest.id}::uuid
            order by rs.is_default desc, array_position(${STAGES}, rs.stage)
          `),
          db.execute<{
            key: string;
            kind: string;
            party: string | null;
            person: string | null;
            label: string | null;
            outcome: string;
            note: string | null;
            declined: boolean;
            seats: number | null;
          }>(sql`
             select distinct on (o.k) o.id as key, x.seats, rs.kind::text, coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label,
               (select coalesce(m.into_id, cy.person_id) from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
                where cy.option_id in (o.id, o.parent_option_id) and cy.role = 'head'
            and not exists (select from candidacy cz where cz.option_id = cy.option_id and cz.role = 'head' and cz.id <> cy.id)
                order by cy.option_id = o.id desc, cy.id limit 1) as person,
               x.kind::text as outcome, coalesce(x.notes, d.notes) as note, d.kind is not null as declined
             from result_set rs
             join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and x.kind <> 'declined'
             join contest_option o on o.id = x.option_id
             left join outcome d on d.result_set_id = rs.id and d.option_id = o.id and d.candidacy_id is null and d.kind = 'declined'
             left join entity_merge pm on pm.entity_id = o.party_id
             where ${isRoot}::boolean and rs.contest_id = ${contest.id}::uuid and ${latest("rs")}
               and (rs.kind <> 'counted' or not exists (select from result_rollup r where r.result_set_k = rs.k offset 0))
             order by o.k, array_position(array['elected', 'won', 'advanced', 'tied', 'withdrew', 'disqualified']::outcome_kind[], x.kind)
          `),
          db.execute<{
            kind: "text" | "summary" | "link";
            body: string | null;
            url: string | null;
            publisher: string;
          }>(sql`
            select distinct on (t.kind) t.kind::text, t.body, coalesce(t.url, s.url) as url, s.publisher
            from ballot_question_text t join source s on s.id = t.source_id
            where ${isRoot}::boolean and t.contest_id = ${contest.id}::uuid
            order by t.kind, t.lang = 'en' desc, s.kind = 'official_body' desc, s.kind = 'community', t.id
          `),
        ]);
      const declaredParties = [
        ...new Set(
          [...declared.rows, ...split.rows]
            .map((row) => row.party)
            .filter((id) => id != null),
        ),
      ];
      const [declaredLabels, declaredColors] = await Promise.all([
        names(
          [
            ...declaredParties,
            ...[...declared.rows, ...split.rows]
              .map((row) => row.person)
              .filter((id) => id != null),
          ],
          contest.date,
        ),
        db.execute<{ party_id: string; color: string }>(sql`
          select p.id as party_id, c.color from unnest(${sql.param(declaredParties)}::uuid[]) p(id),
            lateral (select ${paint(sql`p.id`, sql`${contest.election}::uuid`, sql`${contest.date ?? "infinity"}::date`)} as color) c
          where c.color is not null
        `),
      ]);
      const declaredColor = new Map(
        declaredColors.rows.map((row) => [row.party_id, row.color]),
      );
      const listed =
        isRoot && sets.rows.every((row) => row.status !== "active")
          ? await db.execute<{
              k: number;
              kind: string;
              label: string | null;
              person: string | null;
              party: string | null;
              color: string | null;
              isIndependent: boolean;
              isIncumbent: boolean;
            }>(sql`
              with prior as (
                select pc.id from contest cc
                join election ce on ce.id = cc.election_id
                join contest pc on pc.id <> cc.id and pc.party_scope_id is null
                  and pc.body_id is not distinct from cc.body_id and pc.office_id is not distinct from cc.office_id
                  and pc.district_area_id is not distinct from cc.district_area_id
                join election pe on pe.id = pc.election_id and pe.status = 'held' and pe.date_from < ce.date_from
                where cc.id = ${contest.id}::uuid and (cc.body_id is not null or cc.office_id is not null)
                  and exists (
                    select from outcome x join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
                    where xs.contest_id = pc.id and x.kind in ('elected', 'won')
                  )
                order by pe.date_from desc
                limit 1
              ),
              won as (
                select distinct coalesce(m.into_id, cy.person_id) as person
                from prior
                join contest_option o on o.contest_id = prior.id
                join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                left join entity_merge m on m.entity_id = cy.person_id
                join outcome x on x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
              ),
              covered as (${covered(sql`select ${contest.id}::uuid`)}),
              flagged as (
                select from contest_option fo join candidacy fc on fc.option_id = fo.id and fc.role = 'head'
                where fo.contest_id = ${contest.id}::uuid and fc.is_incumbent is not null
              )
              select o.k, o.kind::text as kind, coalesce(cy.ballot_name, o.ballot_label) as label,
                coalesce(m.into_id, cy.person_id) as person, p.party,
                ${paint(sql`p.party`, sql`${contest.election}::uuid`, sql`${contest.date ?? "infinity"}::date`)} as color,
                coalesce(cy.is_independent, false) as "isIndependent",
                case when exists (select from covered)
                  then cy.person_id is not null and ${tenured(sql`coalesce(m.into_id, cy.person_id)`, sql`${contest.id}::uuid`)}
                  when exists (select from flagged) then coalesce(cy.is_incumbent, false)
                  else ${(contest.date ?? "") > new Date().toISOString().slice(0, 10)}::boolean
                    and coalesce(m.into_id, cy.person_id) in (select person from won)
                end as "isIncumbent"
              from contest_option o
              left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                and not exists (select from candidacy cz where cz.option_id = o.id and cz.role = 'head' and cz.id <> cy.id)
              left join entity_merge m on m.entity_id = cy.person_id
              left join lateral (
                select n.party_id from candidacy_nomination n
                where n.candidacy_id = cy.id and n.kind = 'nominated'
                order by n.party_id
                limit 1
              ) n on true
              left join entity_merge pm on pm.entity_id = coalesce(o.party_id, cy.member_of_party_id, n.party_id)
              cross join lateral (
                select coalesce(pm.into_id, o.party_id, cy.member_of_party_id, n.party_id) as party
              ) p
              where o.contest_id = ${contest.id}::uuid and o.parent_option_id is null
                and o.kind in ('candidate', 'ticket', 'list', 'answer', 'none_of_the_above')
              order by o.ballot_position nulls last, coalesce(cy.ballot_name, o.ballot_label), o.k
            `)
          : { rows: [] };
      const standing = await db.execute<{
        status: "defeated" | "open" | null;
      }>(sql`
        with k as (
          select c.id, c.office_id, c.seat_id, c.body_id, c.district_area_id, c.previous_round_id, e.date_from as d
          from contest c join election e on e.id = c.election_id
          where c.id = ${contest.id}::uuid and c.party_scope_id is null and not c.notional
            and (c.office_id is not null or c.body_id is not null)
        ),
        covered as (${covered(sql`select id from k`)}),
        ran as (
          select o.id as option,
            case when exists (select from covered)
              then ${tenured(sql`coalesce(m.into_id, cy.person_id)`, sql`${contest.id}::uuid`)}
              else coalesce(cy.is_incumbent, false)
            end as "isIncumbent"
          from k join contest_option o on o.contest_id = k.id and o.parent_option_id is null
          join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge m on m.entity_id = cy.person_id
        ),
        decided as (
          select x.option_id, x.kind from outcome x
          join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
          where rs.contest_id = ${contest.id}::uuid and x.candidacy_id is null
            and x.kind in ('elected', 'won', 'advanced')
            and (x.kind <> 'won' or x.seats is null or x.seats = (
              select max(y.seats) from outcome y
              where y.result_set_id = x.result_set_id and y.kind = 'won' and y.candidacy_id is null
            ))
        )
        select case
          when exists (select from decided where kind in ('elected', 'won'))
            and exists (select from ran where "isIncumbent" and option not in (select option_id from decided))
            then 'defeated'
          when not exists (select from ran where "isIncumbent") and k.previous_round_id is null and exists (
            select from tenure t
            left join seat se on se.id = t.seat_id
            where ${holding("t", sql`k.d`)}
              and case when k.office_id is not null then t.office_id = k.office_id
              when k.seat_id is not null then t.seat_id = k.seat_id
              else t.body_id = k.body_id and se.district_area_id = k.district_area_id end
          ) then 'open'
        end as status
        from k
      `);
      const unqualified =
        isRoot && sets.rows.every((row) => row.status !== "active")
          ? (
              await db.execute<{ found: boolean }>(sql`
                select exists (
                  select from label where entity_id = ${contest.id}::uuid and kind = 'ballot' and value = 'no_candidate_qualified'
                ) as found
              `)
            ).rows[0]?.found === true
          : false;
      const listedNames = await names(
        listed.rows.flatMap((row) =>
          [row.person, row.party].filter((id) => id != null),
        ),
        contest.date,
      );
      const today = new Date().toISOString().slice(0, 10);
      const path = slugs.get(contest.id) ?? null;
      const scopePath = path == null ? null : [path, ...unit].join("/");
      const stops = [
        ...ancestors.rows.map((row) => row.area),
        ...chain.map((entry) => entry.id),
      ];
      const away = new Set(
        (
          await db.execute<{ id: string }>(sql`
            select a.id from area a
            where a.id = any(${sql.param(stops.filter((id) => id != null))}::uuid[])
              and (
                a.scheme_code = 'world.country'
                or (
                  exists (select from reporting_unit_area m where m.area_id = a.id)
                  and not exists (
                    select from reporting_unit_area m join reporting_unit r on r.id = m.unit_id
                    where m.area_id = a.id and r.kind <> 'abroad_mission'
                  )
                )
              )
          `)
        ).rows.map((row) => row.id),
      );
      const trail: Array<{
        name: string;
        path: string | null;
        query?: string;
        siblings: {
          contest: string;
          unit: string | null;
          level: string;
          noun: string;
        } | null;
      }> = [
        ...ancestors.rows.map((row, index) => {
          const parent = ancestors.rows[index - 1];
          return {
            name: labels.full.get(row.area ?? row.id) ?? "",
            path: slugs.get(row.id) ?? null,
            siblings:
              parent == null || row.scheme == null
                ? null
                : {
                    contest: parent.id,
                    unit: null,
                    level: row.scheme,
                    noun: row.noun ?? "",
                  },
          };
        }),
        ...chain.map((entry, index) => ({
          name: entry.name,
          path:
            path == null
              ? null
              : `${path}/${unit.slice(0, index + 1).join("/")}`,
          siblings: {
            contest: contest.id,
            unit: index === 0 ? null : unit.slice(0, index).join("/"),
            level: entry.scheme,
            noun: entry.noun,
          },
        })),
      ];
      const entered = stops.findIndex((id) => id != null && away.has(id));
      if (entered !== -1) {
        const before = trail[entered - 1];
        trail.splice(entered, 0, {
          name: m.abroad_crumb(),
          path: before?.path ?? null,
          ...(before?.path != null &&
            chain[entered - ancestors.rows.length] != null && {
              query: `by=${chain[entered - ancestors.rows.length]?.scheme}`,
            }),
          siblings: null,
        });
      }
      const abroad = away.has(stops.at(-1) ?? "")
        ? { electorate: ABROAD[crumbs[0]?.path?.split("/")[0] ?? ""] ?? null }
        : null;
      const page = {
        name: labels.full.get(contest.id) ?? "",
        kind: contest.kind,
        path,
        breadcrumbs: crumbs,
        election: {
          name: labels.full.get(contest.election) ?? "",
          path: slugs.get(contest.election) ?? null,
          date: contest.date,
          year: contest.year,
          status: contest.status,
        },
        area: labels.full.get(contest.place?.id ?? "") ?? "",
        scope: {
          name: trail.at(-1)?.name ?? "",
          noun: scoped?.noun ?? null,
          isFine: scoped?.kind === "precinct" || scoped?.kind === "synthetic",
          parent: isRoot ? null : (trail.at(-2)?.name ?? null),
          home: home.rows[0]?.path ?? null,
          abroad,
          trail,
        },
        series: [],
        notes: notes.rows.map(({ kind, body }) => ({ kind, body })),
        wording: wording.rows.filter(
          (row) =>
            row.kind !== "link" ||
            wording.rows.every(
              (other) => other === row || other.url !== row.url,
            ),
        ),
        children: childIds
          .map((id) => ({
            name: labels.full.get(id) ?? "",
            path: slugs.get(id) ?? null,
          }))
          .toSorted((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true }),
          ),
        places: null,
        sets: sets.rows.map((row) => ({
          stage: row.stage,
          status: row.status,
          completeness: row.completeness,
          isDefault: row.is_default,
          unreconciled: row.unreconciled,
          source: {
            kind: row.kind,
            publisher: row.publisher,
            title: row.title,
            url: row.url,
            license: row.license,
            attribution: row.attribution,
          },
        })),
        citations: cited.rows,
        checks: [],
        incumbency: standing.rows[0]?.status ?? null,
        slate:
          listed.rows.length === 0 && !unqualified
            ? null
            : {
                state:
                  contest.date != null && contest.date > today
                    ? ("scheduled" as const)
                    : contest.date != null &&
                        Date.parse(today) - Date.parse(contest.date) <
                          90 * 86_400_000
                      ? ("counting" as const)
                      : ("unreported" as const),
                isUnqualified: unqualified,
                options: listed.rows.map((row) => ({
                  key: String(row.k),
                  kind: row.kind,
                  label:
                    listedNames.full.get(row.person ?? "") ?? row.label ?? "",
                  party:
                    row.party == null
                      ? null
                      : {
                          id: row.party,
                          name: listedNames.full.get(row.party) ?? "",
                          short: listedNames.short.get(row.party) ?? null,
                          color: row.color,
                        },
                  person: row.person,
                  isIncumbent: row.isIncumbent,
                  isIndependent: row.isIndependent,
                })),
              },
        declared:
          declared.rows[0] == null
            ? null
            : {
                kind: declared.rows[0].kind,
                rows: declared.rows.map((row) => ({
                  key: row.key,
                  label:
                    declaredLabels.full.get(row.person ?? "") ??
                    declaredLabels.full.get(row.party ?? "") ??
                    row.label ??
                    "",
                  short:
                    declaredLabels.short.get(row.party ?? "") ??
                    declaredLabels.full.get(row.party ?? "") ??
                    null,
                  color: declaredColor.get(row.party ?? "") ?? null,
                  person: row.person,
                  party: row.party,
                  outcome: row.outcome,
                  note: row.note,
                  isDeclined: row.declined,
                  seats: row.seats,
                })),
              },
        turnout: roll.turnout,
        quorum: roll.quorum,
        explanations,
        ballot: roll.ballot,
        isStv: roll.isStv,
        channels: Map.groupBy(split.rows, (row) => row.channel)
          .entries()
          .map(([channel, rows]) => {
            let votes = 0;
            const sums = new Map<string, (typeof rows)[number]>();
            for (const row of rows) {
              votes += row.value;
              const key = row.person ?? row.party ?? String(row.option);
              const sum = sums.get(key);
              sums.set(
                key,
                sum == null ? row : { ...sum, value: sum.value + row.value },
              );
            }
            const [lead] = sums
              .values()
              .toArray()
              .toSorted((a, b) => b.value - a.value);
            return {
              code: CHANNELS[channel] ?? "all",
              votes,
              leader:
                lead == null || votes <= 0
                  ? null
                  : {
                      label:
                        declaredLabels.full.get(
                          lead.person ?? lead.party ?? "",
                        ) ?? "",
                      color: declaredColor.get(lead.party ?? "") ?? null,
                      share: lead.value / votes,
                    },
            };
          })
          .toArray()
          .toSorted((a, b) => b.votes - a.votes),
        partial:
          reach.rows.find((row) => row.held > 0 && row.held < row.of) ?? null,
        count: null,
        isDerived: derived,
        current: null,
        electors: null,
        chamber: null,
        partners: [],
        target: null,
        comparison: { label: null, notes: [] },
        rows: [],
        targets: [],
      };
      if (area == null) {
        return withLogos(page, contest.date);
      }

      const covering = await db.execute<{
        id: string;
        series: string;
        election: string;
        indirect: boolean;
        date: string | null;
        year: number | null;
        status: string;
        isRemainder: boolean;
        counted: boolean;
        own: boolean;
        lineage: boolean;
        notional: boolean;
        reach: Array<string> | null;
      }>(sql`
        with recursive anc(id) as (
          select ${area.id}::uuid
          union
          select p.parent_id from area_parent p join anc on p.area_id = anc.id
        ),
        later(id) as (
          select ${contest.place?.id ?? null}::uuid
          union
          select x.id from later cross join lateral (
            select t.area_id as id from area_event_member f
            join area_event e on e.id = f.event_id and e.kind = 'boundary_changed'
            join area_event_member t on t.event_id = e.id and t.role = 'to'
              and (
                (select count(*) from area_event_member n where n.event_id = e.id and n.role = 'from') = 1
                or (select count(*) from area_event_member n where n.event_id = e.id and n.role = 'to') = 1
                or exists (
                  select from area_geometry gf join area_geometry gt on gf.geom && gt.geom
                  where gf.area_id = f.area_id and gt.area_id = t.area_id
                    and ST_Area(ST_Intersection(gf.geom, gt.geom)) > 0.05 * least(ST_Area(gf.geom), ST_Area(gt.geom))
                )
              )
            where f.area_id = later.id and f.role = 'from'
            union
            select b.id from area a
            join crosswalk_weight f on f.from_k = a.k and f.weight >= 0.5
            join crosswalk fc on fc.id = f.crosswalk_id and fc.method = 'area'
            join boundary_set bf on bf.id = fc.from_boundary_set_id
            join boundary_set bt on bt.id = fc.to_boundary_set_id and bt.valid_from > bf.valid_from
            join crosswalk_weight r on r.from_k = f.to_k and r.to_k = a.k and r.weight >= 0.5
            join crosswalk rc on rc.id = r.crosswalk_id and rc.method = 'area'
              and rc.from_boundary_set_id = fc.to_boundary_set_id and rc.to_boundary_set_id = fc.from_boundary_set_id
            join area b on b.k = f.to_k
            where a.id = later.id
            union
            select b.id from area a
            join crosswalk c on c.method in ('exact', 'area', 'population')
            join boundary_set bf on bf.id = c.from_boundary_set_id and bf.scheme_code = a.scheme_code
            join boundary_set bt on bt.id = c.to_boundary_set_id and bt.valid_from > bf.valid_from
            join crosswalk_weight w on w.crosswalk_id = c.id and w.from_k = a.k
            join area b on b.k = w.to_k
            where a.id = later.id
              and (
                w.weight >= 0.5
                or exists (
                  select from crosswalk rc
                  join crosswalk_weight r on r.crosswalk_id = rc.id and r.from_k = w.to_k and r.to_k = a.k and r.weight >= 0.5
                  where rc.from_boundary_set_id = c.to_boundary_set_id and rc.to_boundary_set_id = c.from_boundary_set_id
                    and rc.method = c.method
                )
              )
              and (
                exists (
                  select from name na join name nb on nb.entity_id = b.id and lower(nb.text) = lower(na.text)
                  where na.entity_id = a.id
                )
                or c.method = 'exact' and exists (
                  select from area_geometry ga join area_geometry gb on gb.area_id = b.id
                  where ga.area_id = a.id
                    and least(ST_Area(ga.geom), ST_Area(gb.geom)) >= 0.5 * greatest(ST_Area(ga.geom), ST_Area(gb.geom))
                )
              )
            union
            select b.id from area a
            join crosswalk c on c.method = 'exact'
            join boundary_set bt on bt.id = c.to_boundary_set_id and bt.scheme_code = a.scheme_code
            join boundary_set bf on bf.id = c.from_boundary_set_id and bf.valid_from > bt.valid_from
            join crosswalk_weight w on w.crosswalk_id = c.id and w.to_k = a.k and w.weight >= 0.5
            join area b on b.k = w.from_k
            where a.id = later.id
              and (
                exists (
                  select from name na join name nb on nb.entity_id = b.id and lower(nb.text) = lower(na.text)
                  where na.entity_id = a.id
                )
                or c.method = 'exact' and exists (
                  select from area_geometry ga join area_geometry gb on gb.area_id = b.id
                  where ga.area_id = a.id
                    and least(ST_Area(ga.geom), ST_Area(gb.geom)) >= 0.5 * greatest(ST_Area(ga.geom), ST_Area(gb.geom))
                )
              )
          ) x
        ),
        earlier(id) as (
          select ${contest.place?.id ?? null}::uuid
          union
          select x.id from earlier cross join lateral (
            select f.area_id as id from area_event_member t
            join area_event e on e.id = t.event_id and e.kind = 'boundary_changed'
            join area_event_member f on f.event_id = e.id and f.role = 'from'
              and (
                (select count(*) from area_event_member n where n.event_id = e.id and n.role = 'from') = 1
                or (select count(*) from area_event_member n where n.event_id = e.id and n.role = 'to') = 1
                or exists (
                  select from area_geometry gf join area_geometry gt on gf.geom && gt.geom
                  where gf.area_id = f.area_id and gt.area_id = t.area_id
                    and ST_Area(ST_Intersection(gf.geom, gt.geom)) > 0.05 * least(ST_Area(gf.geom), ST_Area(gt.geom))
                )
              )
            where t.area_id = earlier.id and t.role = 'to'
            union
            select b.id from area a
            join crosswalk_weight f on f.from_k = a.k and f.weight >= 0.5
            join crosswalk fc on fc.id = f.crosswalk_id and fc.method = 'area'
            join boundary_set bf on bf.id = fc.from_boundary_set_id
            join boundary_set bt on bt.id = fc.to_boundary_set_id and bt.valid_from < bf.valid_from
            join crosswalk_weight r on r.from_k = f.to_k and r.to_k = a.k and r.weight >= 0.5
            join crosswalk rc on rc.id = r.crosswalk_id and rc.method = 'area'
              and rc.from_boundary_set_id = fc.to_boundary_set_id and rc.to_boundary_set_id = fc.from_boundary_set_id
            join area b on b.k = f.to_k
            where a.id = earlier.id
            union
            select b.id from area a
            join crosswalk c on c.method in ('exact', 'area', 'population')
            join boundary_set bf on bf.id = c.from_boundary_set_id and bf.scheme_code = a.scheme_code
            join boundary_set bt on bt.id = c.to_boundary_set_id and bt.valid_from < bf.valid_from
            join crosswalk_weight w on w.crosswalk_id = c.id and w.from_k = a.k
            join area b on b.k = w.to_k
            where a.id = earlier.id
              and (
                w.weight >= 0.5
                or exists (
                  select from crosswalk rc
                  join crosswalk_weight r on r.crosswalk_id = rc.id and r.from_k = w.to_k and r.to_k = a.k and r.weight >= 0.5
                  where rc.from_boundary_set_id = c.to_boundary_set_id and rc.to_boundary_set_id = c.from_boundary_set_id
                    and rc.method = c.method
                )
              )
              and (
                exists (
                  select from name na join name nb on nb.entity_id = b.id and lower(nb.text) = lower(na.text)
                  where na.entity_id = a.id
                )
                or c.method = 'exact' and exists (
                  select from area_geometry ga join area_geometry gb on gb.area_id = b.id
                  where ga.area_id = a.id
                    and least(ST_Area(ga.geom), ST_Area(gb.geom)) >= 0.5 * greatest(ST_Area(ga.geom), ST_Area(gb.geom))
                )
              )
            union
            select b.id from area a
            join crosswalk c on c.method = 'exact'
            join boundary_set bt on bt.id = c.to_boundary_set_id and bt.scheme_code = a.scheme_code
            join boundary_set bf on bf.id = c.from_boundary_set_id and bf.valid_from < bt.valid_from
            join crosswalk_weight w on w.crosswalk_id = c.id and w.to_k = a.k and w.weight >= 0.5
            join area b on b.k = w.from_k
            where a.id = earlier.id
              and (
                exists (
                  select from name na join name nb on nb.entity_id = b.id and lower(nb.text) = lower(na.text)
                  where na.entity_id = a.id
                )
                or c.method = 'exact' and exists (
                  select from area_geometry ga join area_geometry gb on gb.area_id = b.id
                  where ga.area_id = a.id
                    and least(ST_Area(ga.geom), ST_Area(gb.geom)) >= 0.5 * greatest(ST_Area(ga.geom), ST_Area(gb.geom))
                )
              )
          ) x
        ),
        roots as (
          select c.id, ${serial("c", "e")} as series,
            e.id as election, e.kind = 'indirect' as indirect, e.date_from, ${CYCLE} as year, e.status,
            c.term is not distinct from 'remainder' as "isRemainder", c.notional,
            c.district_area_id is not distinct from ${contest.place?.id ?? null}::uuid as own,
            c.district_area_id is distinct from ${contest.place?.id ?? null}::uuid
              and c.district_area_id in (select id from later union select id from earlier) as lineage,
            ${reached("c", "e")} as reach
          from (
            select x.id from contest x where x.district_area_id in (select id from anc) and x.allocation_parent_id is null
            union
            select x.id from contest x where x.district_area_id = ${contest.place?.id ?? null}::uuid and x.allocation_parent_id is null
            union
            select x.id from contest x
            where x.district_area_id in (select id from later union select id from earlier) and x.allocation_parent_id is null
            union
            select x.id from polity_territory pt
            join election y on y.polity_id = pt.polity_id and pt.valid @> y.date_from
            join contest x on x.election_id = y.id
            where pt.area_id in (select id from anc) and x.district_area_id is null and x.allocation_parent_id is null
              and y.kind <> 'by_election'
              and not exists (
                select from contest z
                where z.election_id = x.election_id and z.kind = x.kind and z.body_id = x.body_id
                  and z.district_area_id is not null and z.allocation_parent_id is null
              )
            union
            select x.id from contest r
            join contest x on x.body_id = r.body_id and x.district_area_id is null and x.allocation_parent_id is null
            join slug sx on sx.entity_id = x.id and sx.is_current
            where r.id = ${contest.root}::uuid and r.district_area_id is null and r.body_id is not null
              and substring(sx.path from '[^/]+$') = ${path?.split("/").at(-1) ?? ""}
          ) hit
          cross join lateral (select c.* from contest c where c.id = hit.id offset 0) c
          cross join lateral (select e.* from election e where e.id = c.election_id offset 0) e
        ),
        grp(root, id) as materialized (
          select id, id from roots
          union all
          select grp.root, c.id from contest c join grp on c.allocation_parent_id = grp.id
        ),
        tallied as materialized (
          select distinct grp.root from grp
          cross join lateral (
            select from result_set rs
            cross join lateral (
              select from result_rollup x where x.result_set_k = rs.k and x.area_k = ${area.k} and x.measure = any(${COUNTED}) limit 1
            ) probe
            where rs.contest_id = grp.id and rs.status <> 'withdrawn'
            limit 1
          ) hit
        ),
        decided as materialized (
          select distinct grp.root from grp
          cross join lateral (
            select from contest_option o
            join outcome x on x.option_id = o.id and x.kind in ('won', 'elected')
            join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
            where o.contest_id = grp.id
            limit 1
          ) hit
        )
        select * from (
          select r.id, r.series, r.election, r.indirect, r.date_from::text as date, r.year, r.status::text as status,
            r."isRemainder", r.id in (select root from tallied) as counted, r.own, r.lineage, r.notional, r.reach, r.date_from
          from roots r
          where r.id <> ${contest.root}::uuid
        ) r
        where r.counted or (r.id in (select root from decided)
          and not (r.own and ${contest.place?.id ?? null}::uuid not in (select id from anc)))
        order by r.date_from desc nulls last, r.id
      `);
      const gaps = await db.execute<{ stop: string }>(sql`
        select upper(g.valid)::text as stop from area_geometry g
        where g.area_id = ${area.id}::uuid and upper(g.valid) is not null
          and not exists (
            select from area_geometry h
            where h.area_id = g.area_id and h.valid && daterange(upper(g.valid), upper(g.valid) + 365)
          )
          and exists (select from area_geometry h where h.area_id = g.area_id and lower(h.valid) > upper(g.valid))
      `);
      const fence =
        gaps.rows
          .map((row) => row.stop)
          .filter((stop) => contest.date != null && stop <= contest.date)
          .toSorted((a, b) => a.localeCompare(b))
          .at(-1) ?? null;
      const mine = await db.execute<{ reach: Array<string> | null }>(sql`
        select ${reached("c", "e")} as reach from contest c join election e on e.id = c.election_id
        where c.id = ${contest.root}::uuid
      `);
      const scope = (found: Array<string> | null | undefined) =>
        found == null
          ? null
          : found.length === 0 ||
              Map.groupBy(found, (entry) => entry.split(":")[0])
                .values()
                .some((group) => group.length > 1)
            ? ""
            : found.join("|");
      const here = scope(mine.rows[0]?.reach);
      const kept = covering.rows.filter((row) => {
        const there = scope(row.reach);
        return (
          there == null ||
          (there === "" ? here == null || here === "" : there === here)
        );
      });
      const rootIds = kept.map((row) => row.id);
      const held = new Set([
        contest.election,
        ...kept
          .filter(
            (row) => row.series === contest.series && row.own && !row.notional,
          )
          .map((row) => row.election),
      ]);
      const sameRace = kept.filter(
        (row) =>
          row.series === contest.series && (row.own || !held.has(row.election)),
      );
      const comparable = kept.filter(
        (row) => row.counted && row.indirect === contest.indirect,
      );
      const day = contest.date;
      const county = (async () => {
        if (!isRoot || day == null) {
          return null;
        }
        const found = await db.execute<{ polity: string }>(sql`
          select pt.polity_id as polity
          from contest c
          left join boundary_set bs on bs.id = c.boundary_set_id
          join district_county d on d.district_area_id = c.district_area_id
            and d.district_valid @> coalesce(bs.valid_from, ${day}::date) and d.county_valid @> ${day}::date
            and (d.share >= 0.98 or d.rest <= 0.02)
          join polity_territory pt on pt.area_id = d.county_area_id and pt.valid @> ${day}::date
          where c.id = ${contest.root}::uuid and c.district_area_id = ${area.id}::uuid
          limit 1
        `);
        const polity = found.rows[0]?.polity;
        if (polity == null) {
          return null;
        }
        const [labels, slugs] = await Promise.all([
          names([polity], day),
          paths([polity]),
        ]);
        return {
          name: labels.full.get(polity) ?? "",
          path: slugs.get(polity) ?? null,
        };
      })();
      county.catch(() => null);
      const overall = (async () => {
        const parent = ancestors.rows.at(-2);
        const above = isRoot
          ? parent != null &&
            ["office", "delegate_allocation"].includes(parent.kind)
            ? parent
            : null
          : ancestors.rows.at(-1);
        if (above == null || above.kind === "list_allocation") {
          return null;
        }
        const found = await db.execute<{
          person: string | null;
          party: string | null;
          answer: string | null;
          seats: number | null;
        }>(sql`
          select coalesce(m.into_id, cy.person_id) as person, coalesce(pm.into_id, o.party_id) as party,
            case when o.kind = 'answer' then lower(o.ballot_label) end as answer, max(x.seats)::int as seats
          from outcome x
          join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
          join contest_option o on o.id = x.option_id
          left join entity_merge pm on pm.entity_id = o.party_id
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge m on m.entity_id = cy.person_id
          where rs.contest_id = ${above.id}::uuid and x.kind in ('won', 'elected') and x.candidacy_id is null
          group by 1, 2, 3
        `);
        let total = 0;
        for (const row of found.rows) {
          total += row.seats ?? 0;
        }
        const winners = found.rows.some((row) => (row.seats ?? 0) > 1)
          ? found.rows.filter((row) => (row.seats ?? 0) * 2 > total)
          : found.rows;
        return winners.length === 0
          ? null
          : {
              place: labels.full.get(above.area ?? above.id) ?? "",
              winners: winners.map(({ person, party, answer }) => ({
                person,
                party,
                answer,
              })),
            };
      })();
      overall.catch(() => null);
      const origins = (async () => {
        if (
          !isRoot ||
          day == null ||
          sameRace.some(
            (row) =>
              row.date != null &&
              row.date <= day &&
              (fence == null || row.date > fence),
          )
        ) {
          return [];
        }
        const found = await db.execute<{
          id: string;
          area: string;
          date: string;
          year: number | null;
          first: number | null;
          share: number;
        }>(sql`
          with me as (
            select c.body_id, c.office_id, c.kind, c.round_number, c.party_scope_id, c.district_area_id, e.date_from, g.geom
            from contest c join election e on e.id = c.election_id
            cross join lateral (
              select g.geom from area_geometry g
              where g.area_id = c.district_area_id and (upper(g.valid) > e.date_from or upper_inf(g.valid))
              order by lower(g.valid) nulls first limit 1
            ) g
            where c.id = ${contest.root}::uuid and (c.body_id is not null or c.office_id is not null)
          ),
          peers as (
            select c.* from me join contest c
              on c.body_id is not distinct from me.body_id and c.office_id is not distinct from me.office_id
              and c.kind = me.kind and c.round_number = me.round_number
              and c.party_scope_id is not distinct from me.party_scope_id
              and c.allocation_parent_id is null and c.district_area_id is not null and not c.notional
          ),
          before as materialized (
            select max(e.date_from) as day from me, peers c
            join election e on e.id = c.election_id
            where e.date_from < me.date_from and e.kind = 'general'
          )
          select c.id, c.district_area_id as area, e.date_from::text as date, extract(year from e.date_from)::int as year,
            (select min(extract(year from e2.date_from))::int from peers c2 join election e2 on e2.id = c2.election_id
              where c2.district_area_id = c.district_area_id
                and not exists (
                  select from area_geometry x
                  where x.area_id = c2.district_area_id and upper(x.valid) > e2.date_from and upper(x.valid) <= before.day
                    and not exists (
                      select from area_geometry h
                      where h.area_id = x.area_id and h.valid && daterange(upper(x.valid), upper(x.valid) + 365)
                    )
                )) as first,
            (ST_Area(ST_Intersection(g.geom, me.geom, 0.00001)) / ST_Area(me.geom))::float8 as share
          from me, before, peers c
          join election e on e.id = c.election_id
          join area_geometry g on g.area_id = c.district_area_id and g.valid @> e.date_from
          where e.date_from = before.day and e.kind = 'general' and c.district_area_id <> me.district_area_id
            and g.geom && me.geom and ST_Intersects(g.geom, me.geom)
          order by share desc
        `);
        const kept = found.rows.filter((row) => row.share >= 0.01);
        const [labels, slugs] = await Promise.all([
          names(
            kept.map((row) => row.area),
            kept[0]?.date ?? null,
          ),
          paths(kept.map((row) => row.id)),
        ]);
        return kept.map((row) => ({
          name: labels.full.get(row.area) ?? "",
          path: slugs.get(row.id) ?? null,
          share: Math.round(row.share * 100) / 100,
          first: row.first,
          year: row.year,
        }));
      })();
      origins.catch(() => null);
      const counterpart = sameRace.find(
        (row) =>
          isRoot &&
          contest.kind === "office" &&
          row.indirect !== contest.indirect &&
          row.year === contest.year,
      );
      const [rootLabels, rootSlugs, located, tops] = await Promise.all([
        names(rootIds),
        paths(rootIds),
        db.execute<{ root: string; contest: string; depth: number }>(sql`
          with recursive anc(id, depth) as (
            select ${area.id}::uuid, 0
            union all
            select p.parent_id, anc.depth + 1 from area_parent p join anc on p.area_id = anc.id where anc.depth < 8
          ),
          grp(root, id) as (
            select id, id from contest where id = any(${sql.param(sameRace.map((row) => row.id))}::uuid[])
            union all
            select grp.root, c.id from contest c join grp on c.allocation_parent_id = grp.id
          )
          select distinct on (grp.root) grp.root, grp.id as contest, anc.depth
          from grp
          cross join lateral (
            select coalesce(c.district_area_id, (
              select pt.area_id from election e
              join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
              where e.id = c.election_id limit 1
            )) as area
            from contest c where c.id = grp.id offset 0
          ) c
          join anc on anc.id = c.area
          order by grp.root, anc.depth
        `),
        db.execute<{
          root: string;
          person: string | null;
          party: string | null;
          label: string | null;
          kind: string;
          measure: number;
          votes: number;
          total: number;
          color: string | null;
          party_label: string | null;
          party_short: string | null;
        }>(sql`
           with recursive grp(root, id) as (
             select r.id, coalesce((select n.id from contest n where n.previous_round_id = r.id order by n.round_number desc limit 1), r.id)
             from contest r where r.id = any(${sql.param([...sameRace.map((row) => row.id), contest.root])}::uuid[])
             union all
             select grp.root, c.id from contest c join grp on c.allocation_parent_id = grp.id
           ),
           area_k(k) as (
             select ${area.k}::int
             union
             select a.k from contest c join area a on a.id = c.district_area_id
             where c.id = any(${sql.param(sameRace.filter((row) => row.lineage).map((row) => row.id))}::uuid[])
           ),
           here as materialized (
             select distinct x.result_set_k as k from area_k cross join lateral (
               select r.result_set_k from result_rollup_sum r where r.area_k = area_k.k
               union all
               select r.result_set_k from result_rollup_unit r where r.area_k = area_k.k
             ) x
           ),
           sets as materialized (
             select grp.root, rs.k, rs.contest_id, rs.is_default, rs.stage, rs.completeness
             from grp cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs
             where rs.k in (select k from here)
           ),
           picked as (
             select distinct on (rs.contest_id) rs.root, rs.k, hit.area
             from sets rs
             join lateral (
               select x.area from (
                 select ${area.k}::int as area, 0 as rank
                 union all
                 select a.k, 1 from contest c join area a on a.id = c.district_area_id
                 where c.id = rs.root and c.id = any(${sql.param(sameRace.filter((row) => row.lineage).map((row) => row.id))}::uuid[])
               ) x
               where exists (
                 select from result_rollup r where r.result_set_k = rs.k and r.area_k = x.area and r.measure = any(${COUNTED})
               )
               order by x.rank limit 1
             ) hit on true
             order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
           ),
           fetched as (
             select p.root, r.*
             from picked p cross join lateral (
               select r.option_k as option, r.measure, r.value::float8 as value, r.count_round,
                 min(r.count_round) over (partition by r.measure) as first,
                 max(r.count_round) over (partition by r.measure) as last
               from result_rollup r
               where r.result_set_k = p.k and r.area_k = p.area
                 and (r.measure = any(${COUNTED}) and r.option_k <> 0 or r.measure = 2 and r.option_k = 0)
             ) r
           ),
           mains as (
             select root, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main,
               sum(value) filter (where measure = 2 and count_round = first) as appointed
             from fetched group by root
           ),
           tallied as (
             select f.root, m.main, m.appointed, head.person, coalesce(pm.into_id, o.party_id) as party, o.kind::text as kind,
               o.ballot_label as label, f.value,
               coalesce(head.person::text, coalesce(pm.into_id, o.party_id)::text, o.kind::text || coalesce(o.ballot_label, '')) as key
             from fetched f
             join mains m on m.root = f.root and m.main = f.measure
             join contest_option o on o.k = f.option
             left join entity_merge pm on pm.entity_id = o.party_id
             left join lateral (
               select coalesce(em.into_id, cy.person_id) as person
               from candidacy cy left join entity_merge em on em.entity_id = cy.person_id
               where cy.option_id in (o.id, o.parent_option_id) and cy.role = 'head'
          and not exists (select from candidacy cz where cz.option_id = cy.option_id and cz.role = 'head' and cz.id <> cy.id)
               order by cy.option_id = o.id desc, cy.id limit 1
             ) head on true
             where f.count_round = f.last
           ),
           grouped as (
             select root, key, min(main) as measure, min(appointed) as appointed, (array_agg(person))[1] as person,
               (array_agg(party order by party is null, value desc))[1] as party, min(kind) as kind, min(label) as label,
               sum(value) as votes, sum(sum(value)) over (partition by root) as total
             from tallied group by root, key
           ),
           declared as (
             select distinct on (grp.root) grp.root, null::text as key, 1::int2 as measure, null::float8 as appointed,
               head.person, coalesce(pm.into_id, o.party_id) as party, o.kind::text as kind, o.ballot_label as label,
               0::float8 as votes, 0::float8 as total
             from grp
             cross join lateral (select * from contest_option y where y.contest_id = grp.id offset 0) o
             cross join lateral (
               select from outcome x
               where x.option_id = o.id and x.kind in ('won', 'elected')
                 and exists (select from result_set z where z.id = x.result_set_id and ${latest("z")})
               limit 1
             ) won
             left join entity_merge pm on pm.entity_id = o.party_id
             left join lateral (
               select coalesce(em.into_id, cy.person_id) as person
               from candidacy cy left join entity_merge em on em.entity_id = cy.person_id
               where cy.option_id in (o.id, o.parent_option_id) and cy.role = 'head'
               order by cy.option_id = o.id desc, cy.id limit 1
             ) head on true
             where grp.root not in (select root from grouped)
             order by grp.root, o.k
           ),
           tops as (
             select * from (select distinct on (root) * from grouped order by root, votes desc) g
             union all
             select * from declared
           )
           select t.root, t.person, t.label, t.kind, t.measure, t.votes, case when t.measure = 11 then coalesce(t.appointed, t.total) else t.total end as total, x.party,
             ${paint(sql`x.party`, sql`e.id`, sql`e.date_from`)} as color,
             (select n.text from name n where n.entity_id = x.party and n.kind in ('official', 'english')
               order by coalesce(n.valid @> e.date_from, false) desc, n.is_preferred desc, (n.lang = 'en') desc,
                 (n.kind = 'official') desc, n.recorded_from desc, n.id limit 1) as party_label,
             ${abbreviate(sql`x.party`, sql`e.date_from`)} as party_short
           from tops t
           join contest c on c.id = t.root
           join election e on e.id = c.election_id
           cross join lateral (
             select coalesce(t.party, (
               select coalesce(pm.into_id, o.party_id)
               from candidacy cy
               join contest_option o on o.id = cy.option_id and o.party_id is not null
               left join entity_merge pm on pm.entity_id = o.party_id
               join contest oc on oc.id = o.contest_id
               join election oe on oe.id = oc.election_id
               where cy.person_id = any(array(select t.person union all select entity_id from entity_merge where into_id = t.person))
               order by abs(oe.date_from - e.date_from) limit 1
             )) as party
           ) x
        `),
      ]);
      const plural = await db.execute<{
        root: string;
        person: string | null;
        party: string | null;
        color: string | null;
        label: string | null;
        seats: number | null;
        votes: number | null;
      }>(sql`
        select distinct on (c.id, o.k) c.id as root, head.person, x.party, ${paint(sql`x.party`, sql`e.id`, sql`e.date_from`)} as color,
          coalesce(o.ballot_label, o.kind::text) as label, w.seats, (
            select sum(y.value)::float8 from result_rollup y
            where y.result_set_k = rs.k and y.option_k = o.k and y.measure = any(${COUNTED}) and y.count_round in (0, 1)
              and y.area_k = (
                select a.k from area a where a.id = coalesce(c.district_area_id, (
                  select pt.area_id from polity_territory pt where pt.polity_id = e.polity_id and pt.valid @> e.date_from limit 1
                ))
              )
            offset 0
          ) as votes
        from contest c
        join election e on e.id = c.election_id
        join result_set rs on rs.contest_id = c.id and ${latest("rs")}
        join outcome w on w.result_set_id = rs.id and w.kind in ('won', 'elected') and w.candidacy_id is null
        join contest_option o on o.id = w.option_id
        left join entity_merge pm on pm.entity_id = o.party_id
        cross join lateral (select coalesce(pm.into_id, o.party_id) as party) x
        left join lateral (
          select coalesce(em.into_id, cy.person_id) as person
          from candidacy cy left join entity_merge em on em.entity_id = cy.person_id
          where cy.option_id = o.id and cy.role = 'head' order by cy.id limit 1
        ) head on true
        where c.id = any(${sql.param(tops.rows.map((row) => row.root))}::uuid[]) and c.magnitude > 1
        order by c.id, o.k
      `);
      const leaders = await names([
        ...tops.rows.flatMap((row) => (row.person == null ? [] : [row.person])),
        ...plural.rows.flatMap((row) =>
          row.person == null ? [] : [row.person],
        ),
        ...plural.rows.flatMap((row) =>
          row.person == null && row.party != null ? [row.party] : [],
        ),
      ]);
      const seated = Map.groupBy(plural.rows, (row) => row.root);
      const winners = new Map(
        tops.rows.map((row) => [
          row.root,
          {
            key: row.party == null ? `kind:${row.kind}` : `party:${row.party}`,
            label:
              leaders.full.get(row.person ?? "") ??
              row.party_label ??
              row.label ??
              row.kind,
            short: row.party_short,
            color: row.color,
            votes: row.votes,
            share: row.total > 0 ? row.votes / row.total : 0,
            total: row.total,
            measure: MEASURES.get(row.measure) ?? "votes",
            seats: seated
              .get(row.root)
              ?.toSorted(
                (a, b) =>
                  (b.seats ?? 0) - (a.seats ?? 0) ||
                  (b.votes ?? 0) - (a.votes ?? 0),
              )
              .map((seat) => ({
                key:
                  seat.party == null
                    ? `person:${seat.person ?? ""}`
                    : `party:${seat.party}`,
                label:
                  leaders.full.get(seat.person ?? seat.party ?? "") ??
                  seat.label ??
                  "",
                color: seat.color,
              })),
          },
        ]),
      );
      const [locatedSlugs, upper] = await Promise.all([
        paths(located.rows.map((row) => row.contest)),
        db.execute<{ id: string; depth: number }>(sql`
          with recursive anc(id, depth) as (
            select ${area.id}::uuid, 0
            union all
            select p.parent_id, anc.depth + 1 from area_parent p join anc on p.area_id = anc.id where anc.depth < 8
          )
          select id, min(depth) as depth from anc group by id
        `),
      ]);
      const upperLabels = await names(
        upper.rows.map((row) => row.id),
        contest.date,
      );
      const bySegment = new Map(
        chain.map((entry) => [entry.id, entry.segment]),
      );
      const entries = [
        ...sameRace.map((row) => {
          const spot = row.counted
            ? located.rows.find((entry) => entry.root === row.id)
            : undefined;
          const root = rootSlugs.get(row.id) ?? null;
          const isDrilled = !isRoot && row.counted && row.own;
          const base =
            locatedSlugs.get(spot?.contest ?? "") ??
            (root == null
              ? null
              : [root, ...unit.filter(() => isDrilled)].join("/"));
          const below = upper.rows
            .filter((entry) => spot != null && entry.depth < spot.depth)
            .toSorted((a, b) => b.depth - a.depth)
            .map(
              (entry) =>
                bySegment.get(entry.id) ??
                segment(upperLabels.full.get(entry.id) ?? ""),
            );
          return {
            date: row.date,
            year: row.year,
            path: base == null ? null : [base, ...below].join("/"),
            name: rootLabels.full.get(row.id) ?? "",
            current: false,
            indirect: row.indirect,
            annulled: row.status === "annulled",
            isRemainder: row.isRemainder,
            notional: row.notional,
            winner: winners.get(row.id) ?? null,
          };
        }),
        {
          date: contest.date,
          year: contest.year,
          path: scopePath,
          name: labels.full.get(contest.id) ?? "",
          current: true,
          indirect: contest.indirect,
          annulled: contest.status === "annulled",
          isRemainder: contest.isRemainder,
          notional: false,
          winner: winners.get(contest.root) ?? null,
        },
      ];
      const series = entries
        .filter(
          (entry) =>
            entry.current ||
            entries.every(
              (peer) =>
                peer.indirect === entry.indirect ||
                peer.year !== entry.year ||
                (!peer.current && !entry.indirect),
            ),
        )
        .toSorted(
          (a, b) =>
            (a.date ?? "").localeCompare(b.date ?? "") ||
            Number(b.notional) - Number(a.notional) ||
            a.name.localeCompare(b.name, undefined, { numeric: true }),
        )
        .map((entry, index, list) => ({
          ...entry,
          winner:
            entries.find(
              (peer) =>
                !entry.indirect && peer.indirect && peer.year === entry.year,
            )?.winner ?? entry.winner,
          gap: gaps.rows.some((row) => {
            const before = list[index - 1]?.date;
            return (
              before != null &&
              entry.date != null &&
              row.stop > before &&
              row.stop <= entry.date
            );
          }),
        }));
      const [{ chosen, notes: picked }, { chosen: previous }] =
        await Promise.all([
          pick(comparable, { ...contest, fence }, input.vs),
          pick(comparable, { ...contest, fence }, "previous"),
        ]);
      const targets = comparable.map((row) => ({
        value: rootSlugs.get(row.id) ?? row.id,
        label: rootLabels.full.get(row.id) ?? "",
        detail: row.date,
        year: row.year,
        series: row.series === contest.series,
        annulled: row.status === "annulled",
        previous: row.id === previous?.id,
      }));
      const relations = await db.execute<{
        from: string;
        to: string;
        kind: string;
      }>(sql`
        select coalesce(fm.into_id, r.from_party_id) as from, coalesce(tm.into_id, r.to_party_id) as to, r.kind::text
        from party_relation r
        left join entity_merge fm on fm.entity_id = r.from_party_id
        left join entity_merge tm on tm.entity_id = r.to_party_id
        where r.to_party_id is not null
          and (r.date is null or r.date <= greatest(${contest.date ?? "infinity"}::date, ${chosen?.date ?? null}::date))
          and (r.election_id is null or r.election_id in (${contest.election}::uuid, ${chosen?.election ?? null}::uuid))
        union all
        select coalesce(fm.into_id, a.party_id), coalesce(tm.into_id, a.parent_party_id), a.kind::text
        from party_affiliation a
        left join entity_merge fm on fm.entity_id = a.party_id
        left join entity_merge tm on tm.entity_id = a.parent_party_id
        where a.kind = 'regional_branch' and a.valid @> ${contest.date ?? "infinity"}::date
      `);
      const relationLabels = await names([
        ...new Set(relations.rows.map((row) => row.to)),
      ]);
      const lineage: Array<Relation> = relations.rows.map((row) => ({
        ...row,
        toLabel:
          relationLabels.short.get(row.to) ??
          relationLabels.full.get(row.to) ??
          "",
      }));
      const owned = isRoot
        ? null
        : await db.execute<{ id: string }>(sql`
          with recursive grp(id) as (
            select ${contest.id}::uuid
            union all
            select c.id from contest c join grp on c.allocation_parent_id = grp.id
          )
          select c.id from contest c join grp on grp.id = c.id where c.district_area_id = ${area.id}::uuid
        `);
      const loading = load(
        [contest.root],
        [area.k],
        contest.date ?? "infinity",
        owned?.rows.map((row) => row.id) ?? [contest.id],
        [],
        round,
      );
      const [now, before, grid, cast, seats, pairs, steps, shifted] =
        await Promise.all([
          loading,
          chosen == null
            ? null
            : load(
                [chosen.id],
                [area.k],
                chosen.date ?? "infinity",
                [chosen.id].filter(() => isRoot),
                [],
                null,
                "area",
                null,
                round === 1,
              ),
          places(
            contest,
            area,
            isRoot,
            scopePath,
            input.by,
            chosen,
            lineage,
            input.match,
            input.group,
            isShallow && round === last ? 1 : round,
          ).then((found) =>
            found != null && isShallow && round === last
              ? { ...found, isOpening: true }
              : found,
          ),
          counterpart == null
            ? null
            : load(
                [counterpart.id],
                [area.k],
                counterpart.date ?? "infinity",
                [],
              ),
          chamber(contest, area, loading, lineage, isRoot),
          db.execute<{ from: string; to: string }>(
            sisters([contest.root], contest.date ?? "infinity"),
          ),
          tally(
            contest,
            area.k,
            round,
            last,
            lineage,
            input.match,
            input.group,
            elects,
          ),
          redrawn([area.id], contest, chosen),
        ]);
      const locator = (async () => {
        if (!isRoot || contest.place == null) {
          return null;
        }
        const shape = await db.execute<{ isBy: boolean; scheme: string }>(sql`
          select e.kind = 'by_election' as "isBy", a.scheme_code as scheme
          from contest c join election e on e.id = c.election_id join area a on a.id = c.district_area_id
          where c.id = ${contest.root}::uuid
        `);
        const kind = shape.rows[0];
        const cached = await remember(
          kind?.isBy === false
            ? `locator:${contest.election}:${contest.series}:${kind.scheme}:${grid?.level ?? ""}`
            : `locator:${contest.root}:${grid?.level ?? ""}`,
          async () => {
            const found = await db.execute<{
              id: string;
              area: string;
              k: number;
              scheme: string;
              general: string | null;
              since: string | null;
              noun: string;
              party: string | null;
              option: number | null;
              person: string | null;
              label: string | null;
              color: string | null;
              valid: number;
              margin: number | null;
              decided: boolean;
              independent: boolean;
              held: Array<{
                party: string | null;
                option: number;
                person: string | null;
                label: string | null;
                color: string | null;
                independent: boolean;
                seats: number;
              }> | null;
            }>(sql`
              with me as (
                select c.*, a.scheme_code as scheme, e.date_from as day,
                  (
                    select g.id from election g
                    where e.kind = 'by_election' and g.polity_id = e.polity_id and g.kind = 'general' and g.date_from < e.date_from
                    order by g.date_from desc limit 1
                  ) as general
                from contest c join area a on a.id = c.district_area_id join election e on e.id = c.election_id
                where c.id = ${contest.root}::uuid
                  and not exists (
                    select from boundary_set bs
                    where bs.scheme_code = ${grid?.level ?? ""} and bs.valid @> e.date_from
                  )
              ),
              peers as (
                select c.id, a.id as area, a.k, me.scheme, me.day, me.general,
                  coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula', '') = 'stv' as stv
                from me join contest c
                  on (case when me.general is null then c.election_id = me.election_id
                    else c.id = me.id or (c.election_id = me.general and c.district_area_id <> me.district_area_id) end)
                  and c.kind = me.kind and c.round_number = me.round_number
                  and c.body_id is not distinct from me.body_id and c.office_id is not distinct from me.office_id
                  and c.party_scope_id is not distinct from me.party_scope_id and c.allocation_parent_id is null and not c.notional
                join area a on a.id = c.district_area_id and a.scheme_code = me.scheme
                left join electoral_system es on es.id = c.electoral_system_id
              )
              select p.id, p.area, p.k, p.scheme, p.general,
                (select g.date_from::text from election g where g.id = p.general) as since, sc.noun, w.party, w.option, w.person, w.label,
                ${paint(sql`w.party`, sql`${contest.election}::uuid`, sql`p.day`)} as color,
                coalesce(w.valid, 0)::float8 as valid,
                case when w.valid > 0 and not p.stv then ((w.first - w.second) / w.valid)::float8 end as margin,
                coalesce(w.decided, false) as decided,
                coalesce(w.independent, false) as independent,
                (
                  select json_agg(json_build_object(
                    'party', coalesce(nm.into_id, n.party_id), 'option', n.k, 'label', n.ballot_label,
                    'person', (select coalesce(m.into_id, ca.person_id) from candidacy ca
                      left join entity_merge m on m.entity_id = ca.person_id
                      where ca.option_id = n.id and ca.role = 'head' limit 1),
                    'color', ${paint(sql`coalesce(nm.into_id, n.party_id)`, sql`${contest.election}::uuid`, sql`p.day`)},
                    'independent', exists (select from candidacy cy where cy.option_id = n.id and cy.role = 'head' and cy.is_independent),
                    'seats', h.seats
                  ) order by h.seats desc, n.k)
                  from result_set hs
                  cross join lateral (
                    select y.option_id, ${won(sql`hs.id`, sql`y.option_id`)} as seats
                    from (select distinct z.option_id from outcome z where z.result_set_id = hs.id) y
                  ) h
                  join contest_option n on n.id = h.option_id
                  left join entity_merge nm on nm.entity_id = n.party_id
                  where hs.contest_id = p.id and ${latest("hs")} and h.seats > 0
                ) as held
              from peers p
              join scheme sc on sc.code = p.scheme
              left join lateral (
                select coalesce(pm.into_id, o.party_id) as party, o.k as option, o.ballot_label as label,
                  (select coalesce(m.into_id, ca.person_id) from candidacy ca
                    left join entity_merge m on m.entity_id = ca.person_id
                    where ca.option_id = o.id and ca.role = 'head' limit 1) as person,
                  exists (select from candidacy cy where cy.option_id = o.id and cy.role = 'head' and cy.is_independent) as independent,
                  t.first, t.second, t.valid, t.decided
                from (
                  select rs.k, rs.id from result_set rs where rs.contest_id = p.id and ${latest("rs")} limit 1
                ) rs
                cross join lateral (
                  select (array_agg(r.option_k order by r.seats desc, case when r.filled > 1 then r.total else r.value end desc, r.value desc))[1] as top,
                    case when count(*) filter (where r.elected) > 1 then min(r.value) filter (where r.elected) else max(r.value) end as first,
                    case when count(*) filter (where r.elected) > 1 then coalesce(max(r.value) filter (where not r.elected), 0)
                      else coalesce((array_agg(r.value order by r.value desc))[2], 0) end as second,
                    sum(r.value) as valid,
                    bool_or(r.elected) as decided
                  from (
                    select r.*, first_value(r.grp) over (
                      order by r.seats desc, case when r.filled > 1 then r.total else r.value end desc, r.value desc
                    ) as lead
                  from (
                    select r.option_k, r.value, r.won > 0 as elected, r.grp, sum(r.won) over (partition by r.grp) as seats,
                      sum(r.won) over () as filled, sum(r.value) over (partition by r.grp) as total
                    from (
                      select r.option_k, r.value, ${won(sql`rs.id`, sql`ro.id`)} as won,
                        coalesce(ro.party_id::text, case when exists (
                          select from candidacy cy where cy.option_id = ro.id and cy.role = 'head' and cy.is_independent
                        ) then 'independent' else ro.k::text end) as grp
                      from result_rollup r join contest_option ro on ro.k = r.option_k
                      where r.result_set_k = rs.k and r.area_k = p.k and r.option_k <> 0
                        and r.measure = (
                          select x.measure from result_rollup x
                          where x.result_set_k = rs.k and x.area_k = p.k and x.measure = any(${COUNTED})
                          order by array_position(${COUNTED}, x.measure) limit 1
                        )
                        and r.count_round = case when (
                          select count(*) from outcome x where x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                        ) > 1 then (
                          select min(x.count_round) from result_rollup x where x.result_set_k = rs.k and x.area_k = p.k and x.measure = any(${COUNTED})
                        ) else (
                          select max(x.count_round) from result_rollup x where x.result_set_k = rs.k and x.area_k = p.k
                        ) end
                    ) r
                  ) r
                  ) r
                ) t
                join contest_option o on o.k = t.top
                left join entity_merge pm on pm.entity_id = o.party_id
              ) w on true
            `);
            if (found.rows.length < 2) {
              return null;
            }
            const [labels, slugs] = await Promise.all([
              names(
                found.rows.flatMap((row) =>
                  [
                    row.area,
                    row.party,
                    row.person,
                    ...(row.held ?? []).flatMap((entry) => [
                      entry.party,
                      entry.person,
                    ]),
                  ].filter((id) => id != null),
                ),
                contest.date,
              ),
              paths([
                ...found.rows.map((row) => row.id),
                ...found.rows.flatMap((row) =>
                  row.general == null ? [] : [row.general],
                ),
              ]),
            ]);
            return { found, labels, slugs };
          },
        );
        if (cached == null) {
          return null;
        }
        const { found, labels, slugs } = cached;
        const [first] = found.rows;
        if (first == null) {
          return null;
        }
        if (
          new Set(found.rows.map((row) => row.party)).size <= 1 &&
          found.rows.some((row) => row.person != null)
        ) {
          return null;
        }
        const keyed = (row: {
          party: string | null;
          person: string | null;
          option: number | null;
          independent: boolean;
        }) =>
          row.party != null
            ? `party:${row.party}`
            : row.independent
              ? "independent"
              : row.person == null
                ? `option:${row.option ?? ""}`
                : `person:${row.person}`;
        const own = found.rows.find((row) => row.id === contest.root);
        const legend = Map.groupBy(
          [
            ...found.rows.filter(
              (row) => row.option != null && (row.margin !== 0 || row.decided),
            ),
            ...found.rows.flatMap((row) => row.held ?? []),
          ],
          keyed,
        )
          .entries()
          .map(([key, rows]) => ({ key, rows }))
          .toArray()
          .toSorted(
            (a, b) =>
              Number(b.key === (own == null ? "" : keyed(own))) -
                Number(a.key === (own == null ? "" : keyed(own))) ||
              b.rows.length - a.rows.length,
          )
          .map(({ key, rows }, at) => {
            const party = rows[0]?.party ?? null;
            const person = key.startsWith("person:") ? key.slice(7) : null;
            const base = rows[0]?.color ?? null;
            return {
              key,
              label:
                (key === "independent" ? m.seats_independents() : null) ??
                (person == null ? null : labels.full.get(person)) ??
                labels.full.get(party ?? "") ??
                rows[0]?.label ??
                "",
              short:
                person == null ? (labels.short.get(party ?? "") ?? null) : null,
              color:
                person == null
                  ? base
                  : base == null
                    ? (UNAFFILIATED[
                        Number.parseInt(person.slice(-4), 16) %
                          UNAFFILIATED.length
                      ] ?? null)
                    : at === 0
                      ? base
                      : `oklch(from ${base} l max(c, 0.1) calc(h + ${at * 137.5}))`,
              person,
              party,
            };
          });
        const index = new Map(legend.map((item, at) => [item.key, at]));
        return {
          levels: [
            { code: first.scheme, noun: first.noun, count: found.rows.length },
          ],
          level: first.scheme,
          legend,
          context:
            first.general == null || first.since == null
              ? undefined
              : { date: first.since, path: slugs.get(first.general) ?? null },
          rows: found.rows.map((row) => ({
            k: row.k,
            name: labels.full.get(row.area) ?? "",
            path: slugs.get(row.id) ?? null,
            contest: true,
            isRemainder: false,
            valid: row.valid,
            shares: [],
            leader:
              row.option == null || (row.margin === 0 && !row.decided)
                ? null
                : (index.get(keyed(row)) ?? null),
            held: holdings(
              (row.held ?? []).map((entry) => [keyed(entry), entry.seats]),
              index,
            ),
            margin: row.margin,
            earlier: null,
            prior: null,
          })),
          sources: [],
        };
      })();
      locator.catch(() => null);
      const deeper = (async () => {
        if (!isRoot || grid == null || grid.rows.length === 0) {
          return null;
        }
        const active = await db.execute<{ k: number }>(sql`
          select k from result_set where contest_id = ${contest.id}::uuid and status = 'active'
        `);
        const found = await db.execute<{ noun: string }>(sql`
          select sc.noun
          from (
            select distinct unit_k from result_value
            where result_set_k = any(${sql.param(active.rows.map((row) => row.k))}::int[])
          ) x
          join reporting_unit ru on ru.k = x.unit_k and ru.area_relation = 'within' and ru.kind = 'standard'
          join scheme sc on sc.code = ru.scheme_code
          where ru.scheme_code <> all(${sql.param(grid.levels.map((level) => level.code))}::text[])
            and sc.rank > (select rank from scheme where code = ${grid.level})
          group by sc.noun, sc.rank
          having count(*) > ${grid.rows.length}
          order by sc.rank desc
          limit 1
        `);
        return found.rows[0]?.noun ?? null;
      })();
      deeper.catch(() => null);
      const current = now.get(area.k);
      if (
        current != null &&
        current.side.options.every((option) => option.seats == null)
      ) {
        const declared = await db.execute<{ party: string; seats: number }>(sql`
          select coalesce(pm.into_id, o.party_id) as party, sum(r.value)::int as seats
          from contest_option o
          left join entity_merge pm on pm.entity_id = o.party_id
          join result_set rs on rs.contest_id = o.contest_id and ${latest("rs")}
          cross join lateral (
            select r.value from result_rollup r
            where r.result_set_k = rs.k and r.option_k = o.k and r.area_k = ${area.k} and r.measure = 16 and r.count_round = 0 offset 0
          ) r
          where o.contest_id = ${contest.id}::uuid and o.party_id is not null
          group by 1
          having sum(r.value) > 0
        `);
        const held = new Map(
          declared.rows.map((row) => [row.party, row.seats]),
        );
        for (const option of current.side.options) {
          option.seats = held.get(option.party ?? "") ?? null;
        }
      }
      if (current == null) {
        const candidates =
          page.slate?.state === "scheduled" &&
          previous != null &&
          previous.series === contest.series
            ? comparable.filter(
                (row) =>
                  row.series === contest.series &&
                  row.date != null &&
                  contest.date != null &&
                  row.date < contest.date &&
                  (fence == null || row.date > fence) &&
                  row.status !== "annulled" &&
                  row.status !== "canceled",
              )
            : [];
        const prior =
          candidates.length === 0
            ? null
            : ((
                await db.execute<{ id: string; date: string }>(sql`
                  with recursive base as (
                    select c.id from unnest(${sql.param(candidates.map((row) => row.id))}::uuid[]) with ordinality u(id, at)
                    join contest c on c.id = u.id
                    order by c.seat_id is not distinct from (select seat_id from contest where id = ${contest.id}::uuid) desc, u.at
                    limit 1
                  ),
                  chain(id, depth) as (
                    select id, 0 from base
                    union all
                    select c.id, chain.depth + 1 from contest c join chain on c.previous_round_id = chain.id
                  )
                  select chain.id, e.date_from::text as date from chain
                  join contest c on c.id = chain.id join election e on e.id = c.election_id
                  order by chain.depth desc limit 1
                `)
              ).rows[0] ?? null);
        const [held, priorSlugs] =
          prior == null
            ? [null, null]
            : await Promise.all([
                prior.id === chosen?.id
                  ? before
                  : load(
                      [prior.id],
                      [area.k],
                      prior.date ?? "infinity",
                      [prior.id].filter(() => isRoot),
                      [],
                      null,
                      "area",
                      null,
                      true,
                    ),
                paths([prior.id]),
              ]);
        const side = held?.get(area.k)?.side;
        const leaders =
          side == null
            ? []
            : compare(side, null, lineage, "party")
                .flatMap((row) =>
                  row.current == null || row.current.votes <= 0
                    ? []
                    : [{ ...row, cell: row.current }],
                )
                .toSorted((a, b) => b.cell.votes - a.cell.votes)
                .slice(0, 2);
        const leaderNames = await names(
          leaders.flatMap((row) =>
            row.cell.person == null ? [] : [row.cell.person],
          ),
          prior?.date ?? null,
        );
        return withLogos(
          {
            ...page,
            last:
              prior == null || leaders.length === 0
                ? null
                : {
                    date: prior.date,
                    path: priorSlugs?.get(prior.id) ?? null,
                    rows: leaders.map((row) => ({
                      key: row.key,
                      label:
                        leaderNames.full.get(row.cell.person ?? "") ??
                        row.cell.label,
                      person: row.cell.person,
                      party: row.cell.party,
                      color: row.color,
                      share: row.cell.share,
                    })),
                  },
            series,
            places: grid,
            targets,
            origins: await origins,
            county: await county,
            locator: await locator,
            deeper: await deeper,
            overall: await overall,
          },
          contest.date,
        );
      }
      const earlier = before?.get(area.k);
      const flagged = await db.execute<{
        code: string;
        unit: string;
        area: string | null;
        label: string;
        details: Record<string, unknown>;
      }>(sql`
        with recursive flagged as (
          select f.check_code as code, f.details, u.id as unit, u.code as label, u.area_id as area
          from flag f
          join result_set rs on rs.id = f.result_set_id
          join reporting_unit u on u.id = (f.details->>'unit_id')::uuid
          where rs.k = any(${sql.param(current.ks)}::int[]) and f.check_code like 'accounting.%'
        ),
        located(unit, area) as (
          select unit, area from flagged where area is not null
          union
          select m.unit_id, m.area_id from reporting_unit_area m where m.unit_id in (select unit from flagged)
          union
          select located.unit, p.parent_id from area_parent p join located on p.area_id = located.area
        )
        select f.code, f.unit, f.area, f.label, f.details
        from flagged f
        where ${isRoot}::boolean or exists (select 1 from located l where l.unit = f.unit and l.area = ${area.id}::uuid)
        order by f.code, f.label
      `);
      const unitLabels = await names(
        [
          ...new Set(
            flagged.rows.flatMap((row) =>
              [row.unit, row.area].filter((id) => id != null),
            ),
          ),
        ],
        contest.date,
      );
      const checks = Map.groupBy(flagged.rows, (row) => row.code)
        .entries()
        .map(([code, rows]) => ({
          code,
          units: rows.map((row) => ({
            name:
              unitLabels.full.get(row.unit) ??
              unitLabels.full.get(row.area ?? "") ??
              row.label,
            details: Object.fromEntries(
              Object.entries(row.details).filter(
                (entry): entry is [string, number] =>
                  typeof entry[1] === "number",
              ),
            ),
          })),
        }))
        .toArray();
      const notesOut: Array<Note> = [
        ...picked,
        ...[earlier == null ? undefined : shifted.get(area.k)]
          .filter((overlap) => overlap != null)
          .map((overlap) => ({
            code: "redrawn" as const,
            side: null,
            value: String(Math.round(overlap * 100)),
          })),
        ...caveats("current", current.sets),
        ...caveats("target", earlier?.sets ?? []),
      ];
      if (earlier != null) {
        const methods = await db.execute<{
          channel: number;
          isCurrent: boolean;
        }>(sql`
          select distinct r.channel, s."isCurrent"
          from (
            select unnest(${sql.param(current.ks)}::int[]) as k, true as "isCurrent"
            union all
            select unnest(${sql.param(earlier.ks)}::int[]), false
          ) s
          cross join lateral (
            select x.channel from result_rollup x
            where x.result_set_k = s.k and x.area_k = ${area.k}::int and x.channel <> 0 and x.measure = any(${COUNTED}) offset 0
          ) r
        `);
        for (const isCurrent of [true, false]) {
          const only = methods.rows.filter(
            (row) =>
              row.isCurrent === isCurrent &&
              methods.rows.every(
                (other) =>
                  other.isCurrent === isCurrent ||
                  other.channel !== row.channel,
              ),
          );
          if (only.length > 0) {
            notesOut.push({
              code: "channels",
              side: isCurrent ? "current" : "target",
              value: only
                .map((row) => CHANNELS[row.channel] ?? "all")
                .toSorted()
                .join(","),
            });
          }
        }
      }
      const target =
        chosen == null || earlier == null
          ? null
          : {
              name: rootLabels.full.get(chosen.id) ?? "",
              path: rootSlugs.get(chosen.id) ?? null,
              date: chosen.date,
              year: chosen.year,
              status: chosen.status,
              valid: earlier.side.valid,
              complete: earlier.complete,
              sets: earlier.sets,
            };
      const rows = compare(
        current.side,
        earlier?.side ?? null,
        lineage,
        input.match,
        input.group,
      );
      const ballots = [current, cast?.get(area.k)].find(
        (entry) => entry?.measure === "electoral_votes",
      );
      const lines = Map.groupBy(
        current.side.options.toSorted((a, b) => b.votes - a.votes),
        (option) => option.person ?? "",
      );
      const popular = new Map<string, number>();
      const voted = [current, cast?.get(area.k)].find(
        (entry) => entry?.measure === "votes" && entry.side.valid > 0,
      );
      for (const option of voted?.side.options ?? []) {
        if (voted != null && option.person != null) {
          popular.set(
            option.person,
            (popular.get(option.person) ?? 0) + option.votes / voted.side.valid,
          );
        }
      }
      const electors = (
        ballots == null
          ? rows.map((row) => ({
              ...row,
              party: row.current?.party ?? null,
              votes: row.current?.seats ?? 0,
            }))
          : compare(ballots.side, null, lineage, "person").map((row) => {
              const line = lines.get(row.key.slice("person:".length))?.[0];
              return {
                ...row,
                short: line?.short ?? null,
                color: line?.color ?? null,
                party: line?.party ?? null,
                votes: row.current?.votes ?? 0,
              };
            })
      )
        .filter(
          (row) =>
            isRoot &&
            contest.kind === "office" &&
            (contest.isElectoral || ballots != null) &&
            row.votes > 0,
        )
        .map((row) => ({
          key: row.key,
          label: row.label,
          short: row.short,
          color: row.color,
          person: row.current?.person ?? null,
          party: row.party,
          lean: row.lean,
          votes: row.votes,
          popular: popular.get(row.current?.person ?? "") ?? null,
        }))
        .toSorted((a, b) => b.votes - a.votes);
      const electoral = new Map(
        ballots == null ? [] : electors.map((row) => [row.person, row.votes]),
      );
      const [faces, attended, backers, elsewhere] = await Promise.all([
        portraits(
          rows.flatMap(
            (row) => row.current?.person ?? row.earlier?.person ?? [],
          ),
        ),
        attendance(
          page.turnout,
          contest.id,
          area.k,
          current.side.valid,
          chosen?.id ?? null,
          earlier?.side.valid ?? null,
          area.id === contest.place?.id ? null : (contest.place?.id ?? null),
          area.id,
          contest.date,
        ),
        db.execute<{
          person: string;
          kind: string;
          party: string;
          isOwn: boolean;
        }>(sql`
          select distinct coalesce(m.into_id, cy.person_id) as person, n.kind::text as kind,
            coalesce(pm.into_id, n.party_id) as party, n.party_id = o.party_id as "isOwn"
          from candidacy_nomination n
          join candidacy cy on cy.id = n.candidacy_id and cy.role = 'head'
          join contest_option o on o.id = cy.option_id
          left join entity_merge m on m.entity_id = cy.person_id
          left join entity_merge pm on pm.entity_id = n.party_id
          where o.contest_id = ${contest.id}::uuid
        `),
        (async () => {
          const written = rows.flatMap((row) =>
            row.current?.isWriteIn === true && row.current.person != null
              ? [row.current.person]
              : [],
          );
          if (written.length === 0) {
            return new Map<string, { name: string; path: string | null }>();
          }
          const found = await db.execute<{
            person: string;
            contest: string;
            party: string | null;
            place: string | null;
          }>(sql`
            select distinct on (w.person) w.person, k.id as contest, k.party_scope_id as party, k.district_area_id as place
            from unnest(${sql.param(written)}::uuid[]) w(person)
            join candidacy cy on cy.role = 'head' and (
              cy.person_id = w.person or cy.person_id in (select entity_id from entity_merge where into_id = w.person)
            )
            join contest_option o on o.id = cy.option_id and o.kind in ('candidate', 'ticket')
            join contest k on k.id = o.contest_id and k.election_id = ${contest.election}::uuid and k.id <> ${contest.id}::uuid
            order by w.person, k.district_area_id is not distinct from ${contest.place?.id ?? null}::uuid desc, (
                select max(x.value) from result_set rs
                cross join lateral (select x.value from result_rollup x where x.result_set_k = rs.k and x.option_k = o.k offset 0) x
                where rs.contest_id = k.id and ${latest("rs")}
              ) desc nulls last, k.id
          `);
          const ids = found.rows.map((row) => row.contest);
          const [called, linked] = await Promise.all([
            names(
              [
                ...ids,
                ...found.rows.flatMap((row) =>
                  [row.party, row.place].filter((id) => id != null),
                ),
              ],
              contest.date,
            ),
            paths(ids),
          ]);
          return new Map(
            found.rows.map((row) => {
              const party =
                called.short.get(row.party ?? "") ??
                called.full.get(row.party ?? "");
              return [
                row.person,
                {
                  name:
                    party == null
                      ? (called.full.get(row.contest) ?? "").replace(
                          /^\d{4} /u,
                          "",
                        )
                      : row.place != null && row.place === contest.place?.id
                        ? m.ballot_primary_here({ party })
                        : m.ballot_primary({
                            party,
                            place: called.full.get(row.place ?? "") ?? "",
                          }),
                  path: linked.get(row.contest) ?? null,
                },
              ];
            }),
          );
        })(),
      ]);
      const backed = await names(
        [...new Set(backers.rows.map((row) => row.party))],
        contest.date,
      );
      return withLogos(
        {
          ...page,
          notes: notes.rows
            .filter(
              (row) =>
                isRoot ||
                row.set == null ||
                current.ks.includes(row.set) ||
                grid?.sources.includes(row.publisher ?? "") === true,
            )
            .filter(
              (row) =>
                !page.wording.some(
                  (entry) => entry.body?.trim() === row.body.trim(),
                ),
            )
            .map(({ kind, body }) => ({ kind, body })),
          turnout: attended,
          series: series.map((entry) =>
            entry.winner == null
              ? entry
              : {
                  ...entry,
                  winner: {
                    ...entry.winner,
                    color:
                      grid?.legend.find(
                        (item) => item.key === entry.winner?.key,
                      )?.color ?? entry.winner.color,
                    seats: entry.winner.seats?.map((seat) => ({
                      ...seat,
                      color:
                        grid?.legend.find((item) => item.key === seat.key)
                          ?.color ?? seat.color,
                    })),
                  },
                },
          ),
          places: grid,
          declared:
            page.declared == null
              ? null
              : {
                  ...page.declared,
                  rows: page.declared.rows.map((row) => ({
                    ...row,
                    color:
                      grid?.legend.find(
                        (item) =>
                          row.party != null &&
                          item.key === `party:${row.party}`,
                      )?.color ?? row.color,
                  })),
                },
          checks,
          count: steps,
          current: {
            valid: current.side.valid,
            measure: current.measure,
            complete: current.complete,
            sets: current.sets,
            isAllied: current.side.options.some(
              (option) => option.alliance != null && option.votes > 0,
            ),
            isTally: contest.indirect || current.isTally,
          },
          electors:
            electors.length === 0
              ? null
              : {
                  rows: electors,
                  appointed: ballots?.appointed ?? null,
                  link: {
                    path: rootSlugs.get(counterpart?.id ?? "") ?? null,
                    date: counterpart?.date ?? null,
                    popular: counterpart?.indirect === false,
                  },
                },
          chamber:
            seats == null
              ? null
              : {
                  ...seats,
                  parties: seats.parties.map((party) => ({
                    ...party,
                    color:
                      grid?.legend.find((item) => item.key === party.key)
                        ?.color ?? party.color,
                  })),
                },
          partners: pairs.rows.map((row) => [row.from, row.to]),
          target,
          comparison: {
            label:
              target == null
                ? null
                : [current, target].every((side) => side.complete)
                  ? "exact"
                  : "estimated",
            notes: notesOut,
          },
          rows: rows.map((row) => {
            const own = backers.rows
              .filter(
                (entry) =>
                  row.current?.person != null &&
                  entry.person === row.current.person,
              )
              .map((entry) => ({
                kind: entry.kind,
                party: entry.party,
                isOwn: entry.isOwn,
                label:
                  backed.short.get(entry.party) ??
                  backed.full.get(entry.party) ??
                  "",
              }))
              .filter((entry) => entry.label !== "")
              .toSorted(
                (a, b) =>
                  Number(b.isOwn) - Number(a.isOwn) ||
                  a.label.localeCompare(b.label),
              );
            const nominated = own.filter((entry) => entry.kind === "nominated");
            return {
              ...row,
              color:
                grid?.legend.find((item) => item.key === row.key)?.color ??
                row.color,
              current:
                row.current?.seats == null ||
                row.current.person == null ||
                !electoral.has(row.current.person)
                  ? row.current
                  : {
                      ...row.current,
                      seats: electoral.get(row.current.person) ?? 0,
                    },
              photo:
                faces.get(row.current?.person ?? row.earlier?.person ?? "") ??
                null,
              ballot:
                row.current?.isWriteIn === true
                  ? (elsewhere.get(row.current.person ?? "") ?? null)
                  : null,
              nominated:
                nominated.length > 1 || nominated[0]?.isOwn === false
                  ? nominated.map(({ party, label }) => ({ party, label }))
                  : [],
              supported: own
                .filter(
                  (entry) =>
                    entry.kind === "supported" ||
                    (entry.kind === "endorsed" &&
                      !entry.isOwn &&
                      nominated.every((other) => other.party !== entry.party)),
                )
                .map(({ party, label }) => ({ party, label })),
            };
          }),
          targets,
          origins: await origins,
          county: await county,
          locator: await locator,
          deeper: await deeper,
          overall: await overall,
        },
        contest.date,
      );
    }),
  places: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/places",
        summary:
          "List the places within a contest's place, with each one's leader",
      }),
    )
    .input(Input)
    .output(Places)
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const scoped = chain.at(-1);
      const area = scoped ?? contest.place;
      if (area == null) {
        return null;
      }
      const slugs = await paths([contest.id]);
      const path = slugs.get(contest.id) ?? null;
      return places(
        contest,
        area,
        scoped == null,
        path == null
          ? null
          : [path, ...chain.map((entry) => entry.segment)].join("/"),
        input.by,
        null,
        [],
        input.match,
        input.group,
      );
    }),
  ledger: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/ledger",
        summary:
          "List the reporting units with no boundary shapes within a contest's place, each with the place at the given level that holds it",
      }),
    )
    .input(v.object({ ...Input.entries, level: v.optional(v.string()) }))
    .output(
      v.nullable(
        v.object({
          count: v.number(),
          isCapped: v.boolean(),
          folded: v.number(),
          places: Places,
          groups: v.array(
            v.object({
              k: v.nullable(v.number()),
              name: v.string(),
              valid: v.number(),
              size: v.number(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const area = chain.at(-1) ?? contest.place;
      if (area == null) {
        return null;
      }
      const day = contest.date ?? "infinity";
      const CAP = 20_000;
      const sets = await db.execute<{ k: number }>(sql`
        with recursive grp(id) as (
          select ${contest.id}::uuid
          union all
          select c.id from contest c join grp on c.allocation_parent_id = grp.id
        )
        select rs.k from grp cross join lateral (select x.k from result_set x where x.contest_id = grp.id and x.status = 'active' offset 0) rs
      `);
      const ks = sets.rows.map((row) => row.k);
      const valued = await db.execute<{ set: number; k: number }>(sql`
        select s.k as set, u.k from unnest(${sql.param(ks)}::int[]) s(k)
        cross join lateral (
          select distinct v.unit_k as k from result_value v where v.result_set_k = s.k limit ${CAP + 1}
        ) u
      `);
      const finest = Map.groupBy(valued.rows, (row) => row.set)
        .values()
        .toArray()
        .toSorted((a, b) => b.length - a.length)[0];
      const candidates =
        finest == null || finest.length <= CAP
          ? (finest ?? []).map((row) => row.k)
          : chain.length === 0
            ? null
            : (
                await db.execute<{ k: number }>(sql`
                  with recursive down(id, depth) as (
                    select ${area.id}::uuid, 0
                    union all
                    select p.area_id, down.depth + 1 from area_parent p join down on p.parent_id = down.id
                    where p.valid @> ${day}::date and down.depth < 3
                  )
                  select u.k from (select distinct id from down) d
                  join reporting_unit u on u.area_id = d.id
                  where u.area_relation = 'within' and u.kind in ('standard', 'aggregate')
                    and exists (
                      select from result_value v
                      where v.result_set_k = any(${sql.param(ks)}::int[]) and v.unit_k = u.k
                    )
                  limit ${CAP + 1}
                `)
              ).rows.map((row) => row.k);
      if (candidates == null || candidates.length > CAP) {
        return {
          count: CAP,
          isCapped: true,
          folded: 0,
          places: null,
          groups: [],
        };
      }
      const found = await db.execute<{
        k: number;
        id: string;
        code: string;
        scheme: string;
        noun: string;
        kind: string;
        group: string | null;
        at: number | null;
      }>(sql`
        with recursive placed as materialized (
          select u.k, u.id, u.code, u.scheme_code as scheme, u.kind::text as kind, u.area_id, u.parent_id
          from unnest(${sql.param(candidates)}::int[]) c(k)
          join reporting_unit u on u.k = c.k
          where u.area_relation = 'within' and u.kind in ('standard', 'aggregate')
            and u.area_id is not null
            and not exists (
              select from area a join area_geometry g on g.area_id = a.id and g.valid @> ${day}::date
              where a.id = u.area_id and a.scheme_code = u.scheme_code
            )
        ),
        up(unit, id, depth) as (
          select p.k, p.area_id, 0 from placed p
          union all
          select p.k, m.area_id, 0 from placed p
          join reporting_unit_area m on m.unit_id = p.id and not m.approximate and m.valid @> ${day}::date
          union all
          select up.unit, ap.parent_id, up.depth + 1 from up
          join area_parent ap on ap.area_id = up.id and ap.valid @> ${day}::date
          where up.depth < 6 and up.id <> ${area.id}::uuid
        ),
        chains as (
          select up.unit, ${chain.length === 0} or bool_or(up.id = ${area.id}::uuid) as inside,
            (array_agg(a.id order by up.depth) filter (
              where a.scheme_code = ${input.level ?? ""}
                and exists (select from area_geometry ag where ag.area_id = a.id and ag.valid @> ${day}::date)
            ))[1] as "group"
          from up join area a on a.id = up.id
          group by up.unit
        ),
        grouped as (
          select p.*, coalesce(c."group", (
            select o.id from area o
            where o.id = p.area_id and o.id <> ${area.id}::uuid
              and exists (select from area_geometry ag where ag.area_id = o.id and ag.valid @> ${day}::date)
          )) as "group"
          from placed p
          join chains c on c.unit = p.k and c.inside
        )
        select p.k, p.id, p.code, p.scheme, s.noun, p.kind, p."group", g.k as at
        from grouped p
        join scheme s on s.code = p.scheme
        left join area g on g.id = p."group"
        where not exists (select from placed x where x.parent_id = p.id)
      `);
      if (found.rows.length < 2) {
        return null;
      }
      if (found.rows.length > 4000) {
        return {
          count: found.rows.length,
          isCapped: true,
          folded: 0,
          places: null,
          groups: [],
        };
      }
      const labels = await names(
        [
          area.id,
          ...found.rows.flatMap((row) =>
            row.group == null ? [row.id] : [row.id, row.group],
          ),
        ],
        contest.date,
      );
      const units = found.rows
        .filter(
          (row) =>
            row.kind === "standard" ||
            found.rows.every(
              (other) =>
                other.scheme !== row.scheme || other.kind !== "standard",
            ),
        )
        .map((row) => {
          const segments = row.code.split(":");
          const code =
            row.scheme === "us.precinct" && segments.length > 4
              ? segments.slice(4).join(":")
              : (segments.at(-1) ?? row.code);
          const cased = /\p{Ll}/u.test(code)
            ? code
            : code.replaceAll(/\p{Lu}+/gu, (word) =>
                word.length > 2
                  ? word.charAt(0) + word.slice(1).toLowerCase()
                  : word,
              );
          const noun = row.noun.charAt(0).toUpperCase() + row.noun.slice(1);
          const group = labels.full.get(row.group ?? "") ?? null;
          const name =
            labels.full.get(row.id) ??
            (/^\d/u.test(cased)
              ? `${noun} ${cased.replace(/^0+(?=\d)/u, "")}`
              : cased);
          const plain = name.startsWith(`${group ?? ""}, `)
            ? name.slice((group ?? "").length + 2)
            : name;
          return {
            k: row.k,
            at: row.at,
            group,
            noun: row.noun,
            scheme: row.scheme,
            name: (plain.charAt(0).toUpperCase() + plain.slice(1)).replace(
              /(?<=\D)0+(?=\d)/u,
              "",
            ),
            path: null,
            isContest: false,
            owner: null,
          };
        });
      const tabulated = await tabulate(
        [contest.root],
        units,
        contest.date,
        null,
        [],
        input.match,
        input.group,
        input.round === 0 ? null : input.round,
        "unit",
      );
      const kept = tabulated.rows.flatMap((row, index) => {
        const own = units[index];
        return own == null || (row.valid === 0 && row.declared == null)
          ? []
          : [{ row: { ...row, at: null }, own }];
      });
      if (kept.length < 2) {
        return null;
      }
      const grouped = Map.groupBy(kept, (entry) => entry.own.at)
        .entries()
        .map(([k, entries]) => {
          let valid = 0;
          for (const { row } of entries) {
            valid += row.valid;
          }
          return {
            k,
            name: entries[0]?.own.group ?? labels.full.get(area.id) ?? "",
            valid,
            entries: entries.toSorted((a, b) =>
              entries.length > 10
                ? b.row.valid - a.row.valid
                : a.row.name.localeCompare(b.row.name, undefined, {
                    numeric: true,
                  }),
            ),
          };
        })
        .toArray()
        .toSorted(
          (a, b) =>
            Number(a.k == null) - Number(b.k == null) || b.valid - a.valid,
        );
      const shown = grouped.filter(
        (entry) => entry.k == null || entry.entries.length > 1,
      );
      if (shown.length === 0) {
        return null;
      }
      const nouns = Map.groupBy(
        shown.flatMap((entry) => entry.entries),
        (entry) => entry.own.noun,
      )
        .entries()
        .toArray()
        .toSorted((a, b) => b[1].length - a[1].length);
      return {
        count: kept.length,
        isCapped: false,
        folded: grouped.length - shown.length,
        places: {
          levels: nouns.map(([noun, entries]) => ({
            code: `unit:${entries[0]?.own.scheme ?? ""}`,
            noun,
            count: entries.length,
          })),
          level: `unit:${nouns[0]?.[1][0]?.own.scheme ?? ""}`,
          ...tabulated,
          rows: shown.flatMap((entry) => entry.entries.map(({ row }) => row)),
        },
        groups: shown.map((entry) => ({
          k: entry.k,
          name: entry.name,
          valid: entry.valid,
          size: entry.entries.length,
        })),
      };
    }),
  adjacent: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/adjacent",
        summary:
          "List which of the given places border a place, with boundaries as of the contest's date",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        k: v.number(),
        ks: v.array(v.number()),
      }),
    )
    .output(v.array(v.number()))
    .handler(async ({ input }) => {
      const found = await db.execute<{ k: number }>(sql`
        select distinct a.k
        from contest c
        join election e on e.id = c.election_id
        join area o on o.k = ${input.k}
        join area_geometry og on og.area_id = o.id and og.valid @> e.date_from
        join area a on a.k = any(${sql.param(input.ks)}::int[]) and a.k <> o.k
        join area_geometry g on g.area_id = a.id and g.valid @> e.date_from
        where c.id = ${input.id}::uuid and st_dwithin(g.geom, og.geom, 0.002)
      `);
      return found.rows.map((row) => row.k);
    }),
  context: os
    .meta(
      openapi({
        method: "GET",
        path: "/places/{k}/context",
        summary:
          "List the larger places that locate a place on a map, smallest first: the counties, cities, and towns that contain it, up to its state (its country when the place is a state), each with a simplified outline in map coordinates and the frame that fits it",
      }),
    )
    .input(
      v.object({
        k: v.pipe(
          v.union([v.number(), v.string()]),
          v.transform(Number),
          v.integer(),
        ),
      }),
    )
    .output(
      v.array(
        v.object({
          k: v.number(),
          name: v.string(),
          tiles: v.nullable(v.string()),
          outline: v.string(),
          extent: v.object({
            x: v.number(),
            y: v.number(),
            width: v.number(),
            height: v.number(),
          }),
          frame: v.object({
            west: v.number(),
            south: v.number(),
            east: v.number(),
            north: v.number(),
          }),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        k: number;
        day: string;
        size: number;
        outline: string;
        tiles: string | null;
        west: number;
        south: number;
        east: number;
        north: number;
      }>(sql`
        with recursive up(id, depth) as (
          select a.id, 0 from area a where a.k = ${input.k}
          union all
          select p.parent_id, up.depth + 1 from up
          cross join lateral (
            select x.parent_id from area_parent x where x.area_id = up.id
            order by upper(x.valid) desc nulls first limit 1
          ) p
          where up.depth < 6
        ),
        sized as (
          select up.depth, a.id, a.scheme_code as scheme, g.geom, g.valid,
            (ST_XMax(g.geom) - ST_XMin(g.geom)) * (ST_YMax(g.geom) - ST_YMin(g.geom)) as size
          from up join area a on a.id = up.id
          cross join lateral (
            select geom, valid from area_geometry where area_id = a.id
            order by upper(valid) desc nulls first limit 1
          ) g
        ),
        own as (select * from sized where depth = 0),
        cap as (
          select b.* from sized b cross join own o
          where b.depth > 0 and b.size >= o.size * 4
            and b.depth >= (select max(depth) from sized) - 1
          order by b.depth limit 1
        ),
        around as (
          select a.id, a.scheme_code as scheme, g.geom, g.valid,
            (ST_XMax(g.geom) - ST_XMin(g.geom)) * (ST_YMax(g.geom) - ST_YMin(g.geom)) as size
          from (
            select distinct f.area_id from own o
            join area_geometry f on f.area_id <> o.id and f.geom ~ ST_Expand(
              Box2D(o.geom),
              (ST_XMin(o.geom) - ST_XMax(o.geom)) * 0.2,
              (ST_YMin(o.geom) - ST_YMax(o.geom)) * 0.2
            )::geometry
          ) f
          join area a on a.id = f.area_id
          join scheme s on s.code = a.scheme_code
          cross join own o
          cross join cap
          cross join lateral (
            select geom, valid from area_geometry where area_id = a.id
            order by valid @> coalesce(upper(o.valid) - 1, current_date) desc,
              upper(valid) desc nulls first
            limit 1
          ) g
          where s.kind = 'administrative' and s.polity_id is not null
            and a.scheme_code not in ('us.school_district', 'us.special_district')
            and a.id not in (select id from sized)
            and g.geom && o.geom
            and (ST_XMax(g.geom) - ST_XMin(g.geom)) * (ST_YMax(g.geom) - ST_YMin(g.geom))
              between o.size * 1.5 and cap.size
            and ST_Area(ST_Intersection(ST_ClipByBox2D(g.geom, o.geom), o.geom))
              >= ST_Area(o.geom) * 0.6
        ),
        chosen as (
          select distinct on (c.id) c.* from (
            select id, scheme, geom, valid, size from sized where depth > 0
            union all
            select id, scheme, geom, valid, size from around
          ) c
          cross join own o cross join cap
          where c.size >= o.size * 1.5 and c.size <= cap.size
          order by c.id
        ),
        planed as materialized (
          select id, valid, size, ${plane("geom", "scheme")} as geom
          from (
            select c.id, c.valid, c.size, c.scheme, coalesce(
              (select ST_Simplify(coalesce(x.geom, c.geom), c.t) from display.clipped x
                join display.meta dm on dm.mask_version = x.mask_version
                where x.area_id = c.id and x.valid = c.valid and x.geom_hash = md5(ST_AsBinary(c.geom))),
              case when c.size <= 4 then (
                select case when ST_IsEmpty(w.d) then null else w.d end from (
                  select case
                    when not exists (select from display.lake l where l.geom && v.g and ST_Intersects(l.geom, v.g))
                      and exists (select from display.land k where k.geom && v.g and ST_Covers(k.geom, v.g)) then v.g
                    else ST_Multi(ST_CollectionExtract(ST_Difference(
                      coalesce((select ST_UnaryUnion(ST_Collect(ST_Intersection(ST_MakeValid(ST_Simplify(ST_ClipByBox2D(k.geom, v.g), c.t, true)), v.g)))
                        from display.land k where k.geom && v.g), ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)),
                      coalesce((select ST_UnaryUnion(ST_Collect(ST_MakeValid(ST_Simplify(ST_ClipByBox2D(l.geom, v.g), c.t, true))))
                        from display.lake l where l.geom && v.g), ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326))), 3))
                  end as d
                  from (select ST_MakeValid(ST_Simplify(c.geom, c.t, true)) as g) v
                ) w
              ) end,
              ST_Simplify(c.geom, c.t)) as geom
            from (select *, least(sqrt(size) / 500, 0.01) as t from chosen) c
          ) c
        ),
        measured as (
          select id, valid, size, geom,
            (ST_XMax(geom) - ST_XMin(geom)) * (ST_YMax(geom) - ST_YMin(geom)) as extent
          from planed
        ),
        drawn as (
          select id, size, coalesce(upper(valid) - 1, current_date) as day, ST_Scale((
            select ST_Collect(d.geom) from ST_Dump(ST_SimplifyPreserveTopology(geom, sqrt(extent) / 250)) d
            where ST_Area(d.geom) >= extent / 20000
          ), 0.001, 0.001) as geom
          from measured
        )
        select a.id, a.k, drawn.day::text, drawn.size, ST_AsSVG(drawn.geom, 1, 2) as outline,
          ST_XMin(drawn.geom) as west, ST_YMin(drawn.geom) as south,
          ST_XMax(drawn.geom) as east, ST_YMax(drawn.geom) as north,
          (
            select coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) from boundary_set bs
            where bs.scheme_code = a.scheme_code and bs.valid @> drawn.day
            order by bs.valid_from desc nulls last limit 1
          ) as tiles
        from drawn join area a on a.id = drawn.id where drawn.geom is not null
      `);
      const rungs: typeof found.rows = [];
      for (const row of found.rows.toSorted((a, b) => b.size - a.size)) {
        const last = rungs.at(-1);
        if (last == null || row.size * 3 <= last.size) {
          rungs.push(row);
        }
      }
      const [frames, labels] = await Promise.all([
        Promise.all(rungs.map((row) => framing(row.id, row.day))),
        names(
          rungs.map((row) => row.id),
          rungs[0]?.day ?? null,
        ),
      ]);
      return rungs
        .flatMap((row, index) => {
          const frame = frames[index];
          return frame == null
            ? []
            : [
                {
                  k: row.k,
                  name: labels.full.get(row.id) ?? "",
                  tiles: stored(row.tiles),
                  outline: row.outline,
                  extent: {
                    x: row.west,
                    y: -row.north,
                    width: row.east - row.west,
                    height: row.north - row.south,
                  },
                  frame,
                },
              ];
        })
        .toReversed();
    }),
  boundaries: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/boundaries",
        summary:
          "Pick the boundary set a contest's place and the places within it are drawn from, with the shapes' quality, sources, and places without a shape",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.optional(v.string()),
        level: v.optional(v.string()),
      }),
    )
    .output(
      v.array(
        v.object({
          scheme: v.string(),
          noun: v.string(),
          vintage: v.nullable(v.string()),
          asOf: v.nullable(v.string()),
          tiles: v.nullable(v.string()),
          isPending: v.boolean(),
          isApproximate: v.boolean(),
          approximate: v.array(v.number()),
          note: v.nullable(v.string()),
          sources: v.array(
            v.object({
              publisher: v.string(),
              title: v.string(),
              url: v.nullable(v.string()),
              attribution: v.nullable(v.string()),
            }),
          ),
          count: v.number(),
          missing: v.array(v.string()),
          k: v.nullable(v.number()),
          blank: v.array(
            v.object({
              k: v.number(),
              name: v.string(),
              isHeld: v.boolean(),
              isOutside: v.boolean(),
              isPending: v.boolean(),
            }),
          ),
          outlying: v.optional(
            v.array(
              v.object({
                k: v.number(),
                code: v.optional(v.string()),
                group: v.picklist(["territory", "overseas", "abroad"]),
              }),
            ),
          ),
          parted: v.optional(
            v.object({ isOnly: v.boolean(), ks: v.array(v.number()) }),
          ),
          frame: v.nullable(
            v.object({
              west: v.number(),
              south: v.number(),
              east: v.number(),
              north: v.number(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const area = chain.at(-1) ?? contest.place;
      if (area == null) {
        return [];
      }
      const day = contest.date ?? "infinity";
      const [own, kids] = await Promise.all([
        db.execute<{ scheme: string; shaped: string | null }>(sql`
          select a.scheme_code as scheme, bs.as_of::text as shaped
          from area a
          left join contest c on c.id = ${contest.id}::uuid
          left join boundary_set bs on bs.id = c.boundary_set_id and bs.scheme_code = a.scheme_code
          where a.id = ${area.id}::uuid
        `),
        input.level == null
          ? []
          : children(
              contest.id,
              area,
              chain.length === 0,
              day,
              undefined,
              true,
            ),
      ]);
      const scheme = own.rows[0]?.scheme ?? "";
      const listed = kids
        .filter((kid) => kid.scheme === input.level)
        .map((kid) => kid.id);
      const nested = await db.execute<{ scheme: string; id: string }>(sql`
        select a.scheme_code as scheme, a.id
        from contest c
        join area a on a.id = c.district_area_id
        join area_parent p on p.area_id = a.id and p.valid @> ${day}::date
        where c.allocation_parent_id = ${contest.id}::uuid
          and p.parent_id = any(${sql.param(listed)}::uuid[])
          and a.scheme_code <> ${input.level ?? ""}
      `);
      const groups = [
        { scheme, ids: [area.id] },
        ...(input.level == null || input.level === scheme
          ? []
          : [{ scheme: input.level, ids: listed }]),
        ...Map.groupBy(nested.rows, (row) => row.scheme)
          .entries()
          .map(([code, rows]) => ({
            scheme: code,
            ids: rows.map((row) => row.id),
          })),
      ].filter((group) => group.ids.length > 0);
      const whole =
        input.level == null
          ? []
          : await universe(contest.id, input.level, area.id, day);
      const present = new Set(
        kids.filter((kid) => kid.scheme === input.level).map((kid) => kid.k),
      );
      const absent = whole.filter((row) => !present.has(row.k));
      const offside =
        input.level == null
          ? []
          : await outside(
              input.level,
              area.id,
              [...present, ...whole.map((row) => row.k)],
              day,
            );
      const off = new Set(offside.map((entry) => entry.k));
      const [frame, blankLabels] = await Promise.all([
        framing(
          area.id,
          own.rows[0]?.shaped ?? day,
          [
            ...present,
            ...whole.filter((row) => !row.outside).map((row) => row.k),
          ].filter((k) => !off.has(k)),
        ),
        names(
          absent.map((row) => row.id),
          contest.date,
        ),
      ]);
      const blank = absent
        .map((row) => ({
          k: row.k,
          name: blankLabels.full.get(row.id) ?? "",
          isHeld: row.held,
          isOutside: row.outside,
          isPending: row.pending,
        }))
        .toSorted((a, b) => a.name.localeCompare(b.name));
      const drawn = await Promise.all(
        groups.map(async (group) => {
          const found = await db.execute<{
            noun: string;
            vintage: string | null;
            asOf: string | null;
            tiles: string | null;
            shaped: Array<string>;
            qualities: Array<string>;
            approximate: Array<number>;
            sources: Array<{
              publisher: string;
              title: string;
              url: string | null;
              attribution: string | null;
            }> | null;
            note: string | null;
          }>(sql`
            with chosen as (
              select bs.vintage, bs.as_of, coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles
              from boundary_set bs
              where bs.scheme_code = ${group.scheme}
                and (bs.id = (select boundary_set_id from contest where id = ${contest.id}::uuid) or bs.valid @> ${day}::date)
              order by bs.id = (select boundary_set_id from contest where id = ${contest.id}::uuid) desc, bs.valid_from desc nulls last
              limit 1
            ),
            shapes as (
              select g.area_id, g.quality::text as quality, g.source_id
              from area_geometry g
              left join chosen on true
              where g.area_id = any(${sql.param(group.ids)}::uuid[]) and g.valid @> coalesce(chosen.as_of, ${day}::date)
            )
            select sc.noun, chosen.vintage, chosen.as_of::text as "asOf", chosen.tiles,
              array(select distinct area_id::text from shapes) as shaped,
              array(select distinct quality from shapes) as qualities,
              array(
                select a.k from (select area_id, valid from area_geometry where quality = 'synthetic' offset 0) g
                join area a on a.id = g.area_id
                left join chosen on true
                where (g.area_id = any(${sql.param(group.ids)}::uuid[])
                  or a.k = any(${sql.param(group.scheme === input.level ? blank.map((entry) => entry.k) : [])}::int[]))
                  and g.valid @> coalesce(chosen.as_of, ${day}::date)
              ) as approximate,
              (select json_agg(json_build_object('publisher', s.publisher, 'title', s.title, 'url', s.url, 'attribution', nullif(s.attribution, '')) order by s.publisher)
               from source s where s.id in (select source_id from shapes)) as sources,
              (select n.body_md from note n
               where n.kind = 'methodology' and n.entity_id = ${area.id}::uuid
                 and ${area.id}::uuid = any(${sql.param(group.ids)}::uuid[])
               limit 1) as note
            from scheme sc left join chosen on true
            where sc.code = ${group.scheme}
          `);
          const row = found.rows[0];
          if (row == null || (row.vintage == null && row.shaped.length === 0)) {
            return [];
          }
          const shaped = new Set(row.shaped);
          const absent = group.ids.filter((id) => !shaped.has(id));
          const isLevel = group.scheme === input.level;
          const [labels, outlying, split] = await Promise.all([
            names(absent, contest.date),
            isLevel && group.scheme === "us.state"
              ? db.execute<{ k: number; code: string }>(sql`
                select a.k, (array['AS', 'GU', 'MP', 'PR', 'VI'])[array_position(array['60', '66', '69', '72', '78'], c.code)] as code from area a join area_code c on c.area_id = a.id
                where (a.id = any(${sql.param(group.ids)}::uuid[]) or a.k = any(${sql.param(blank.map((entry) => entry.k))}::int[]))
                  and c.code in ('60', '66', '69', '72', '78')
              `)
              : null,
            parted(group.scheme, area.id, row.asOf ?? day),
          ]);
          const strip = [
            ...(outlying?.rows ?? []).map((entry) => ({
              ...entry,
              group: "territory" as const,
            })),
            ...(isLevel ? offside : []),
          ];
          return [
            {
              scheme: group.scheme,
              noun: row.noun,
              vintage: row.vintage,
              asOf: row.asOf,
              tiles:
                input.level != null &&
                projectionOf(input.level) !== projectionOf(group.scheme)
                  ? null
                  : stored(row.tiles),
              isPending: row.tiles != null && stored(row.tiles) == null,
              isApproximate: row.qualities.includes("synthetic"),
              approximate: row.approximate,
              note: row.note,
              sources: row.sources ?? [],
              count: group.ids.length,
              missing: absent
                .map((id) => labels.full.get(id) ?? "")
                .toSorted((a, b) => a.localeCompare(b)),
              k: group.ids.includes(area.id) ? area.k : null,
              frame: group.ids.includes(area.id) ? frame : null,
              blank: isLevel ? blank : [],
              ...(strip.length > 0 && { outlying: strip }),
              parted: split,
            },
          ];
        }),
      );
      return drawn.flat();
    }),
  atlas: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/atlas",
        summary:
          "Every place at one level within a contest's place, as compact columns for a map, with each place's shares and an optional comparison",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.optional(v.string()),
        level: v.optional(v.string()),
        vs: v.optional(v.string()),
        group: Input.entries.group,
        whole: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.object({
        title: v.string(),
        scope: v.object({
          name: v.string(),
          k: v.number(),
          unit: v.string(),
          path: v.nullable(v.string()),
        }),
        trail: v.array(v.object({ name: v.string(), unit: v.string() })),
        date: v.nullable(v.string()),
        outline: v.nullable(v.string()),
        frame: v.nullable(
          v.object({
            west: v.number(),
            south: v.number(),
            east: v.number(),
            north: v.number(),
          }),
        ),
        levels: v.array(
          v.object({
            code: v.string(),
            noun: v.string(),
            count: v.number(),
            tiles: v.nullable(v.string()),
          }),
        ),
        level: v.nullable(v.string()),
        parts: v.array(
          v.object({ k: v.number(), name: v.string(), unit: v.string() }),
        ),
        targets: v.array(
          v.object({ id: v.string(), label: v.string(), date: v.string() }),
        ),
        target: v.nullable(v.string()),
        estimated: v.boolean(),
        isPending: v.boolean(),
        isAllied: v.boolean(),
        legend: v.array(
          v.object({
            key: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
          }),
        ),
        places: v.object({
          k: v.array(v.number()),
          name: v.array(v.string()),
          valid: v.array(v.number()),
          x: v.array(v.nullable(v.number())),
          y: v.array(v.nullable(v.number())),
          leader: v.array(v.nullable(v.number())),
          held: v.optional(
            v.array(v.nullable(v.array(v.tuple([v.number(), v.number()])))),
          ),
          prior: v.array(v.nullable(v.number())),
          shares: v.array(v.array(v.nullable(v.number()))),
          before: v.array(v.array(v.nullable(v.number()))),
          redrawn: v.array(v.nullable(v.number())),
          status: v.optional(v.array(v.nullable(v.string()))),
        }),
      }),
    )
    .handler(({ input }) => atlas(input)),
  mosaic: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/mosaic",
        summary:
          "Every place at the finest levels with area-ID raster tiles across a contest's place, as compact columns for a map that shows its finest results at any zoom; k is delta-coded and shares are per mille",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        vs: v.optional(v.string()),
        group: Input.entries.group,
      }),
    )
    .output(
      v.object({
        target: v.nullable(v.string()),
        legend: v.array(
          v.object({
            key: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
          }),
        ),
        layers: v.array(
          v.object({ code: v.string(), noun: v.string(), tiles: v.string() }),
        ),
        places: v.object({
          k: v.array(v.number()),
          name: v.array(v.string()),
          valid: v.array(v.number()),
          part: v.array(v.nullable(v.number())),
          layer: v.array(v.number()),
          leader: v.array(v.nullable(v.number())),
          held: v.optional(
            v.array(v.nullable(v.array(v.tuple([v.number(), v.number()])))),
          ),
          prior: v.array(v.nullable(v.number())),
          shares: v.array(v.array(v.nullable(v.number()))),
          before: v.array(v.array(v.nullable(v.number()))),
        }),
      }),
    )
    .handler(async ({ input }) => {
      const vs = (input.vs ?? "none").toLowerCase();
      if (input.group !== "option") {
        return mosaic(input);
      }
      if (vs !== "none") {
        const twin = path.resolve(
          "../../data/mosaics",
          `${input.id.toLowerCase()}.vs.json`,
        );
        if (existsSync(twin)) {
          const held = JSON.parse(await readFile(twin, "utf8")) as {
            path: string | null;
            sheet: Awaited<ReturnType<typeof mosaic>>;
          };
          if (
            held.sheet.layers.every((layer) => layer.noun != null) &&
            ["previous", held.sheet.target, held.path].some(
              (value) => value?.toLowerCase() === vs,
            )
          ) {
            return held.sheet;
          }
        }
        return mosaic(input);
      }
      const file = path.resolve(
        "../../data/mosaics",
        `${input.id.toLowerCase()}.json`,
      );
      const open = async () =>
        existsSync(file)
          ? (
              JSON.parse(await readFile(file, "utf8")) as {
                sheet: Partial<Awaited<ReturnType<typeof mosaic>>>;
              }
            ).sheet
          : null;
      const held = await open();
      if (
        held?.places?.part != null &&
        held.target !== undefined &&
        held.layers?.every((layer) => layer.noun != null) === true
      ) {
        return held as Awaited<ReturnType<typeof mosaic>>;
      }
      await rebuildMosaics([input.id.toLowerCase()]);
      const built = await open();
      if (built == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return built as Awaited<ReturnType<typeof mosaic>>;
    }),
  lean: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/lean",
        summary:
          "How a place within a contest leans against its parent and the contest's whole area: margins between the contest's two leading options (first minus second) wherever both stood, the previous comparable election's margin, and its rank among same-level siblings. Where an alliance's parties took turns standing so that options leave gaps, it compares alliances instead and says so in group",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.pipe(v.string(), v.minLength(1)),
        group: v.optional(v.picklist(["option", "alliance"])),
      }),
    )
    .output(
      v.object({
        group: v.picklist(["option", "alliance"]),
        axis: v.array(
          v.object({
            key: v.string(),
            label: v.string(),
            color: v.nullable(v.string()),
          }),
        ),
        place: v.object({
          name: v.string(),
          margin: v.number(),
          before: v.nullable(v.number()),
        }),
        parent: v.nullable(v.object({ name: v.string(), margin: v.number() })),
        whole: v.object({ name: v.string(), margin: v.number() }),
        siblings: v.object({
          noun: v.string(),
          count: v.number(),
          total: v.number(),
          rank: v.number(),
          places: v.array(
            v.object({
              name: v.string(),
              margin: v.number(),
              isOwn: v.boolean(),
            }),
          ),
        }),
        previous: v.nullable(
          v.object({ label: v.string(), date: v.nullable(v.string()) }),
        ),
      }),
    )
    .handler(async ({ input }) => lean(input)),
  rounds: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/rounds",
        summary:
          "List every round of a contest, from a primary or first round through its runoff, with each round's leader at the contest's place or the scoped place within it",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.optional(v.string()),
      }),
    )
    .output(
      v.array(
        v.object({
          id: v.string(),
          path: v.nullable(v.string()),
          round: v.number(),
          kind: v.string(),
          date: v.nullable(v.string()),
          isCurrent: v.boolean(),
          leaders: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              color: v.nullable(v.string()),
              person: v.nullable(v.string()),
              party: v.nullable(v.string()),
              share: v.number(),
              outcome: v.nullable(v.string()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        path: string | null;
        round: number;
        kind: string;
        date: string | null;
      }>(sql`
        with recursive back(id) as (
          select ${input.id}::uuid
          union
          select c.previous_round_id from contest c join back on c.id = back.id where c.previous_round_id is not null
        ),
        chain(id) as (
          select id from back
          union
          select c.id from contest c join chain on c.previous_round_id = chain.id
        )
        select c.id, (select s.path from slug s where s.entity_id = c.id and s.is_current) as path,
          c.round_number as round, e.kind::text as kind, e.date_from::text as date
        from chain join contest c on c.id = chain.id join election e on e.id = c.election_id
        order by e.date_from, c.round_number
      `);
      if (found.rows.length < 2) {
        return [];
      }
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const area = chain.at(-1) ?? contest.place;
      const unit = chain.map((entry) => entry.segment).join("/");
      const built = await Promise.all(
        found.rows.map(async (row) => {
          const [all, lineage] = await Promise.all([
            area == null
              ? null
              : load([row.id], [area.k], row.date ?? "infinity", [row.id]),
            branches(row.date ?? "infinity"),
          ]);
          const loaded = area == null ? undefined : all?.get(area.k);
          const leaders =
            loaded == null
              ? []
              : compare(loaded.side, null, lineage, "party")
                  .flatMap((entry) =>
                    entry.current == null
                      ? []
                      : [
                          {
                            key: entry.key,
                            label: entry.label,
                            color: entry.color,
                            person: entry.current.person,
                            party: entry.current.party,
                            votes: entry.current.votes,
                            share: entry.current.share,
                            outcome:
                              chain.length > 0 ? null : entry.current.outcome,
                          },
                        ],
                  )
                  .filter((entry) => entry.share > 0)
                  .toSorted((a, b) => b.votes - a.votes);
          const moved = leaders.filter((entry) =>
            ["advanced", "won", "elected"].includes(entry.outcome ?? ""),
          );
          return {
            id: row.id,
            path:
              row.path == null
                ? null
                : [
                    row.path,
                    ...(loaded == null || unit === "" ? [] : [unit]),
                  ].join("/"),
            round: row.round,
            kind: row.kind,
            date: row.date,
            isCurrent: row.id === input.id,
            leaders: moved.length > 3 ? moved : leaders.slice(0, 3),
          };
        }),
      );
      const colors = new Map<string, string | null>();
      const ordered = built.toSorted(
        (a, b) => Number(b.isCurrent) - Number(a.isCurrent),
      );
      for (const round of ordered) {
        for (const leader of round.leaders) {
          if (!colors.has(leader.key)) {
            colors.set(leader.key, leader.color);
          }
        }
      }
      return built.map((round) => ({
        ...round,
        leaders: round.leaders.map((leader) => ({
          ...leader,
          color: colors.get(leader.key) ?? leader.color,
        })),
      }));
    }),
  ballot: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/ballot",
        summary:
          "List the other contests decided on the same day with results for the scoped place, each linked at that place with its top two",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.optional(v.string()),
      }),
    )
    .output(
      v.array(
        v.object({
          id: v.string(),
          name: v.string(),
          path: v.string(),
          leaders: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              color: v.nullable(v.string()),
              person: v.nullable(v.string()),
              party: v.nullable(v.string()),
              share: v.number(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const area = chain.at(-1);
      if (area == null) {
        return [];
      }
      const day = contest.date ?? "infinity";
      const found = await db.execute<{
        id: string;
        parent: string | null;
        path: string;
      }>(sql`
        with recursive up(id, parent) as (
          select e.id, e.parent_id from contest c join election e on e.id = c.election_id where c.id = ${contest.id}::uuid
          union all
          select e.id, e.parent_id from election e join up on e.id = up.parent
        ),
        tree(id) as (
          select id from up where parent is null
          union all
          select k.id from election k join tree on k.parent_id = tree.id
        ),
        own(id) as (
          select ${contest.root}::uuid
          union all
          select c.id from contest c join own on c.allocation_parent_id = own.id
        ),
        candidates as materialized (
          select c.id, c.allocation_parent_id as parent
          from contest c
          join tree on tree.id = c.election_id
          join election e on e.id = c.election_id and e.date_from = ${day}::date
          where c.id not in (select id from own) and not c.notional
        ),
        grp(id, top) as (
          select id, id from candidates
          union all
          select g.id, grp.top from contest g join grp on g.allocation_parent_id = grp.id
        ),
        hits as (
          select distinct grp.top from grp
          cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs
          where exists (
            select 1 from result_rollup r
            where r.result_set_k = rs.k and r.area_k = ${area.k} and r.measure = any(${COUNTED}) offset 0
          )
        )
        select c.id, c.parent, s.path
        from candidates c
        join hits h on h.top = c.id
        join slug s on s.entity_id = c.id and s.is_current
      `);
      const parents = new Set(found.rows.map((row) => row.parent));
      const kept = found.rows.filter((row) => !parents.has(row.id));
      const labels = await names(
        kept.map((row) => row.id),
        contest.date,
      );
      const built = await Promise.all(
        kept.map(async (row) => {
          const other = await find(row.id);
          if (other.place == null) {
            return null;
          }
          const kids =
            other.place.k === area.k
              ? []
              : await children(row.id, other.place, true, day, area.k);
          const unit =
            other.place.k === area.k
              ? ""
              : kids.find((kid) => kid.k === area.k)?.segment;
          if (unit == null) {
            return null;
          }
          const [totals, lineage] = await Promise.all([
            load([row.id], [area.k], day, [row.id]),
            branches(day),
          ]);
          const loaded = totals.get(area.k);
          return {
            id: row.id,
            name: labels.full.get(row.id) ?? "",
            path: [row.path, unit].filter((part) => part !== "").join("/"),
            leaders:
              loaded == null
                ? []
                : compare(loaded.side, null, lineage, "party")
                    .flatMap((entry) =>
                      entry.current == null
                        ? []
                        : [
                            {
                              key: entry.key,
                              label: entry.label,
                              color: entry.color,
                              person: entry.current.person,
                              party: entry.current.party,
                              votes: entry.current.votes,
                              share: entry.current.share,
                            },
                          ],
                    )
                    .toSorted((a, b) => b.votes - a.votes)
                    .slice(0, 2),
          };
        }),
      );
      return built
        .filter((entry) => entry != null)
        .toSorted((a, b) =>
          a.name.localeCompare(b.name, undefined, { numeric: true }),
        );
    }),
  cycle: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/cycle",
        summary:
          "Trace a contest's cycle: the party primaries that chose the nominees for its general election, that general election, and the other races on the ballot the same day",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        unit: v.optional(v.string()),
      }),
    )
    .output(
      v.object({
        general: v.nullable(Step),
        primaries: v.array(Step),
        others: v.array(
          v.object({
            party: PartyRef,
            races: v.array(
              v.object({
                place: v.string(),
                path: v.nullable(v.string()),
                isCurrent: v.boolean(),
              }),
            ),
          }),
        ),
        day: v.object({
          date: v.nullable(v.string()),
          path: v.nullable(v.string()),
          place: v.string(),
          part: v.nullable(Link),
          groups: v.array(
            v.object({
              kind: v.picklist(GROUPS),
              label: v.nullable(v.string()),
              count: v.number(),
              path: v.nullable(v.string()),
              anchor: v.nullable(v.string()),
            }),
          ),
        }),
      }),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const focus =
        chain.at(-1) ?? (contest.id === contest.root ? null : contest.place);
      const same = (a: string, b: string) =>
        sql.raw(`((${a}.office_id is not null and ${b}.office_id = ${a}.office_id)
          or (${a}.office_id is null and ${b}.office_id is null and ${b}.body_id = ${a}.body_id
            and ${b}.district_area_id is not distinct from ${a}.district_area_id))
          and (${a}.seat_id is null or ${b}.seat_id is null or ${b}.seat_id = ${a}.seat_id)
          and (${a}.term is null or ${b}.term is null or ${b}.term = ${a}.term)`);
      const [stepped, day] = await Promise.all([
        db.execute<{
          id: string;
          party: string | null;
          district: string | null;
          date: string | null;
          until: string | null;
          isGeneral: boolean;
          isSame: boolean;
          isUmbrella: boolean;
          runoff: string | null;
          runoffDate: string | null;
        }>(sql`
          with recursive back(id, prev) as (
            select id, previous_round_id from contest where id = ${contest.root}::uuid
            union all
            select c.id, c.previous_round_id from contest c join back on c.id = back.prev
          ),
          r as materialized (
            select c.*, e.kind as ekind, e.date_from as d
            from contest c join election e on e.id = c.election_id where c.id = ${contest.root}::uuid
          ),
          g as materialized (
            (select c.*, e.date_from as d
              from back join contest c on c.id = back.id join election e on e.id = c.election_id cross join r
              where r.party_scope_id is null and r.ekind not in ('primary', 'caucus')
                and e.kind not in ('primary', 'caucus', 'runoff', 'indirect')
              order by e.date_from limit 1)
            union all
            (select c.*, e.date_from
              from r cross join contest c join election e on e.id = c.election_id
              where (r.party_scope_id is not null or r.ekind in ('primary', 'caucus'))
                and c.party_scope_id is null and c.allocation_parent_id is null and c.previous_round_id is null
                and e.kind not in ('primary', 'caucus', 'runoff', 'indirect')
                and e.date_from between r.d and r.d + 400 and (r.office_id is not null or r.body_id is not null)
                and ${same("r", "c")}
              order by e.date_from limit 1)
          ),
          prev as (
            select coalesce(max(e.date_from), (select d from g limit 1) - 400) as d
            from g cross join contest c join election e on e.id = c.election_id
            where c.party_scope_id is null and c.allocation_parent_id is null and c.previous_round_id is null
              and e.kind not in ('primary', 'caucus', 'runoff', 'indirect')
              and e.date_from < g.d and e.date_from >= g.d - 400 and ${same("g", "c")}
          ),
          picked as materialized (
            select c.id, c.party_scope_id as party, c.district_area_id as district, e.date_from as d,
              c.id = g.id as "isGeneral", c.district_area_id is not distinct from g.district_area_id as "isSame"
            from g cross join prev cross join contest c join election e on e.id = c.election_id
            where c.id = g.id or (
              c.party_scope_id is not null and c.allocation_parent_id is null and c.previous_round_id is null
              and e.kind in ('primary', 'caucus') and e.date_from > prev.d and e.date_from <= g.d
              and (g.office_id is not null or g.body_id is not null) and ${same("g", "c")}
            )
          )
          select p.id, p.party, p.district, p.d::text as date, p."isGeneral", p."isSame",
            u.until::text as until, u.until is not null as "isUmbrella",
            n.id as runoff, n.date::text as "runoffDate"
          from picked p
          left join lateral (
            with recursive grp(id) as (
              select c.id from contest c where c.allocation_parent_id = p.id
              union all
              select c.id from contest c join grp on c.allocation_parent_id = grp.id
            )
            select max(e.date_from) as until from grp join contest c on c.id = grp.id join election e on e.id = c.election_id
            having count(*) > 0
          ) u on true
          left join lateral (
            select n.id, e.date_from as date from contest n join election e on e.id = n.election_id
            where n.previous_round_id = p.id and e.kind = 'runoff'
            order by e.date_from limit 1
          ) n on true
          order by p.d, p.id
        `),
        db.execute<{
          kind: (typeof GROUPS)[number];
          owner: string | null;
          count: number;
          sample: string;
          isCurrent: boolean;
          isHead: boolean;
          isPart: boolean;
          top: string;
          topPolity: string;
          election: string;
          isScoped: boolean;
        }>(sql`
          with recursive up(id, parent, depth) as (
            select e.id, e.parent_id, 0 from contest c join election e on e.id = c.election_id where c.id = ${contest.id}::uuid
            union all
            select e.id, e.parent_id, up.depth + 1 from election e join up on e.id = up.parent
          ),
          scope as (
            select s.id, s.id <> (select id from up where parent is null) as scoped, e.polity_id
            from (select coalesce((
              select up.id from up join election e on e.id = up.id join polity y on y.id = e.polity_id
              where y.kind in ('federal_unit', 'region') order by up.depth desc limit 1
            ), (select id from up where parent is null)) as id) s
            join election e on e.id = s.id
          ),
          tree(id) as (
            select id from scope
            union all
            select k.id from election k join tree on k.parent_id = tree.id
          ),
          roots as materialized (
            select c.id, c.kind, c.body_id, c.office_id, o.kind as okind, y.kind as ykind,
              y.id = (select t.polity_id from election t where t.id = (select id from up where parent is null))
                or (y.id = (select x.polity_id from election x where x.id = (select election_id from contest where id = ${contest.id}::uuid))
                  and y.kind not in ('sovereign_state', 'supranational', 'historical_state', 'federal_unit', 'region')) as own,
              y.id is distinct from (select t.polity_id from election t where t.id = (select id from up where parent is null)) as part
            from tree
            join election e on e.id = tree.id and e.date_from = ${contest.date ?? "infinity"}::date
            join contest c on c.election_id = e.id and c.allocation_parent_id is null and not c.notional
            left join office o on o.id = c.office_id
            left join body b on b.id = c.body_id
            join polity y on y.id = coalesce(b.polity_id, o.polity_id, e.polity_id)
          ),
          classed as (
            select id, okind, part, case
                when kind = 'ballot_question' then case
                  when ykind in ('sovereign_state', 'supranational', 'historical_state') then 'national_measure'
                  when ykind in ('federal_unit', 'region') then 'regional_measure'
                  else 'local_measure' end
                when ykind in ('sovereign_state', 'supranational', 'historical_state') or own then 'owner'
                when okind = 'governor' then 'governor'
                when ykind in ('federal_unit', 'region') and kind <> 'office' then 'legislature'
                when ykind in ('federal_unit', 'region') then 'regional_office'
                else 'local' end as kind,
              case when kind <> 'ballot_question' and (ykind in ('sovereign_state', 'supranational', 'historical_state') or own)
                then coalesce(office_id, body_id)
                when okind = 'governor' and (select scoped from scope) then office_id
                when kind not in ('ballot_question', 'office')
                  and (ykind = 'region' or (ykind = 'federal_unit' and (select scoped from scope))) then body_id end as owner
            from roots
          )
          select kind, owner, count(*)::int as count, min(id::text) as sample,
            bool_or(id = ${contest.root}::uuid) as "isCurrent", bool_or(okind = 'head_of_state') as "isHead",
            bool_and(part) as "isPart",
            (select id from scope) as top,
            (select polity_id from scope) as "topPolity",
            (select scoped from scope) as "isScoped",
            (select election_id from contest where id = ${contest.id}::uuid) as election
          from classed group by kind, owner
        `),
      ]);
      const general = stepped.rows.find((row) => row.isGeneral) ?? null;
      const scoped = stepped.rows.filter(
        (row) => !row.isGeneral && row.party != null,
      );
      const ancestry =
        focus == null
          ? null
          : await db.execute<{ id: string }>(sql`
            with recursive anc(id) as (
              select ${focus.id}::uuid
              union
              select p.parent_id from area_parent p join anc on p.area_id = anc.id
            )
            select id from anc
          `);
      const anchored = new Set(ancestry?.rows.map((row) => row.id));
      const placing =
        anchored.size === 0
          ? null
          : await db.execute<{ top: string; id: string }>(sql`
            with recursive grp(id, top) as (
              select c.id, c.allocation_parent_id from contest c
              where c.allocation_parent_id = any(${sql.param(stepped.rows.map((row) => row.id))}::uuid[])
              union all
              select c.id, grp.top from contest c join grp on c.allocation_parent_id = grp.id
            )
            select distinct on (grp.top) grp.top, grp.id
            from grp join contest c on c.id = grp.id
            where c.district_area_id = any(${sql.param([...anchored])}::uuid[])
            order by grp.top, grp.id
          `);
      const located = new Map(
        placing?.rows.map((row) => [row.top, row.id] as const),
      );
      const featured = scoped.filter(
        (row) =>
          row.isSame ||
          row.isUmbrella ||
          (row.district != null && anchored.has(row.district)),
      );
      const others = scoped.filter((row) => !featured.includes(row));
      const steps = [...(general == null ? [] : [general]), ...featured];
      const decided = [
        ...steps.map((row) => row.id),
        ...steps.flatMap((row) => row.runoff ?? []),
      ];
      const won = await db.execute<{
        contest: string;
        person: string | null;
        party: string | null;
        label: string | null;
        isTop: boolean;
        share: number | null;
      }>(sql`
        select x.contest, cy.person_id as person,
          coalesce((
            select coalesce(go.party_id, (
              select l.party_id from contest_option l
              where l.parent_option_id = go.id and l.party_id is not null
              order by l.ballot_position nulls last, l.k limit 1
            ))
            from candidacy gc join contest_option go on go.id = gc.option_id
            where gc.person_id = cy.person_id and gc.role = 'head' and go.parent_option_id is null
              and go.contest_id = ${general?.id ?? null}::uuid
            limit 1
          ), o.party_id, (
            select l.party_id from contest_option l
            where l.parent_option_id = o.id and l.party_id is not null
            order by l.ballot_position nulls last, l.k limit 1
          )) as party,
          coalesce(cy.ballot_name, o.ballot_label) as label,
          oc.seats is null or oc.seats = max(oc.seats) over (partition by x.contest) as "isTop",
          (
            select (sum(r.value) filter (where r.option_k = any(array(
                select l.k from contest_option l where l.id = o.id or l.parent_option_id = o.id
              ))))::float8 / nullif(sum(r.value), 0)::float8
            from result_rollup r
            where r.result_set_k = rs.k and r.area_k = spot.k and r.option_k <> 0
              and r.measure = (
                select y.measure from result_rollup y
                where y.result_set_k = rs.k and y.area_k = spot.k and y.measure = any(${COUNTED})
                order by array_position(${COUNTED}, y.measure) limit 1
              )
              and r.count_round = (
                select max(y.count_round) from result_rollup y where y.result_set_k = rs.k and y.area_k = spot.k
              )
          ) as share
        from unnest(${sql.param(decided)}::uuid[]) as x(contest)
        cross join lateral (
          select rs.id, rs.k from result_set rs where rs.contest_id = x.contest
          order by rs.is_default desc, array_position(${STAGES}, rs.stage)
          limit 1
        ) rs
        cross join lateral (
          select a.k from contest c join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          where c.id = x.contest limit 1
        ) spot
        join outcome oc on oc.result_set_id = rs.id and oc.candidacy_id is null and oc.kind in ('won', 'elected')
          and not exists (select from outcome d where d.result_set_id = rs.id and d.option_id = oc.option_id and d.kind = 'declined')
        join contest_option o on o.id = oc.option_id
        left join lateral (
          select c.person_id, c.ballot_name from candidacy c
          where c.option_id = o.id and c.role = 'head' order by c.id limit 1
        ) cy on true
        order by x.contest, o.ballot_position nulls last, o.k
      `);
      const tallied = await db.execute<{
        contest: string;
        party: string | null;
        votes: number | null;
      }>(sql`
        select x.contest, coalesce(o.party_id, (
            select l.party_id from contest_option l
            where l.parent_option_id = o.id and l.party_id is not null
            order by l.ballot_position nulls last, l.k limit 1
          )) as party, (
            select sum(r.value) from result_rollup r
            where r.result_set_k = rs.k and r.area_k = spot.k
              and r.option_k = any(array(
                select l.k from contest_option l where l.id = o.id or l.parent_option_id = o.id
              ))
              and r.measure = (
                select y.measure from result_rollup y
                where y.result_set_k = rs.k and y.area_k = spot.k and y.measure = any(${COUNTED})
                order by array_position(${COUNTED}, y.measure) limit 1
              )
              and r.count_round = (
                select max(y.count_round) from result_rollup y where y.result_set_k = rs.k and y.area_k = spot.k
              )
          )::float8 as votes
        from unnest(${sql.param(steps.map((row) => row.id))}::uuid[]) as x(contest)
        cross join lateral (
          select rs.k from result_set rs where rs.contest_id = x.contest
          order by rs.is_default desc, array_position(${STAGES}, rs.stage)
          limit 1
        ) rs
        cross join lateral (
          select a.k from contest c join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          where c.id = x.contest limit 1
        ) spot
        join contest_option o on o.contest_id = x.contest and o.parent_option_id is null
      `);
      const finalists = tallied.rows
        .filter((row) => row.contest === general?.id)
        .toSorted((a, b) => (b.votes ?? 0) - (a.votes ?? 0));
      let cast = 0;
      for (const row of finalists) {
        cast += row.votes ?? 0;
      }
      const major = new Set(
        finalists
          .filter(
            (row, index) =>
              row.votes != null && (index < 2 || row.votes >= cast * 0.1),
          )
          .flatMap((row) => row.party ?? []),
      );
      const turnout = featured
        .map((row) => {
          let votes = 0;
          for (const entry of tallied.rows) {
            if (entry.contest === row.id) {
              votes += entry.votes ?? 0;
            }
          }
          return { id: row.id, votes };
        })
        .filter((row) => row.votes > 0)
        .toSorted((a, b) => b.votes - a.votes);
      const kept = new Set(
        major.size > 0
          ? featured
              .filter((row) => major.has(row.party ?? ""))
              .map((row) => row.id)
          : turnout.slice(0, 2).map((row) => row.id),
      );
      const locals = located.values().toArray();
      const lone = day.rows.filter(
        (row) =>
          row.count === 1 &&
          !row.isCurrent &&
          [
            "owner",
            "legislature",
            "regional_office",
            "local",
            "local_measure",
          ].includes(row.kind),
      );
      const here = focus?.id ?? contest.place?.id;
      const away =
        lone.length === 0 || here == null
          ? new Set<string>()
          : new Set(
              (
                await db.execute<{ id: string }>(sql`
                  select x.id from unnest(${sql.param(lone.map((row) => row.sample))}::uuid[]) as x(id)
                  join contest c on c.id = x.id
                  join election e on e.id = c.election_id
                  left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
                  cross join lateral (
                    select g.geom from area_geometry g
                    where g.area_id = coalesce(c.district_area_id, pt.area_id) and g.valid @> e.date_from limit 1
                  ) a
                  cross join lateral (
                    select g.geom from area_geometry g
                    where g.area_id = ${here}::uuid and g.valid @> e.date_from limit 1
                  ) h
                  where not st_relate(a.geom, h.geom, 'T********')
                `)
              ).rows.map((row) => row.id),
            );
      const groups = day.rows.filter(
        (row) => !(row.isCurrent && row.count === 1) && !away.has(row.sample),
      );
      const top = day.rows[0]?.top ?? null;
      const election = day.rows[0]?.election ?? null;
      const topPolity = day.rows[0]?.topPolity ?? null;
      const isScoped = day.rows[0]?.isScoped ?? false;
      const parties = [
        ...steps.flatMap((row) => row.party ?? []),
        ...others.flatMap((row) => row.party ?? []),
        ...won.rows.flatMap((row) => row.party ?? []),
      ];
      const [labels, slugs, colors] = await Promise.all([
        names(
          [
            ...won.rows.flatMap((row) => row.person ?? []),
            ...others.flatMap((row) => row.district ?? []),
            ...locals,
            ...groups.flatMap((row) => row.owner ?? []),
            ...groups.map((row) => row.sample),
            ...[election, topPolity].filter((id) => id != null),
          ],
          contest.date,
        ),
        paths([
          ...stepped.rows.map((row) => row.id),
          ...stepped.rows.flatMap((row) => row.runoff ?? []),
          ...locals,
          ...groups.map((row) => row.sample),
          ...[top, election].filter((id) => id != null),
        ]),
        refs(parties, contest.date),
      ]);
      const placed = await db.execute<{ id: string; area: string }>(sql`
        select c.id, coalesce(c.district_area_id, pt.area_id) as area
        from contest c join election e on e.id = c.election_id
        left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
        where c.id = any(${sql.param(locals)}::uuid[])
      `);
      const places = await names(
        placed.rows.map((row) => row.area),
        contest.date,
      );
      const area = new Map(
        placed.rows.map((row) => [row.id, places.full.get(row.area) ?? ""]),
      );
      const winners = (id: string) =>
        won.rows
          .filter((row) => row.contest === id && row.isTop)
          .map((row) => ({
            label:
              labels.full.get(row.person ?? "") ??
              row.label ??
              colors.get(row.party ?? "")?.label ??
              "",
            person: row.person,
            party: row.party,
            color: colors.get(row.party ?? "")?.color ?? null,
            share: row.share,
          }));
      const step = (row: (typeof stepped.rows)[number]) => {
        const local = located.get(row.id);
        return {
          id: row.id,
          path: slugs.get(row.id) ?? null,
          date: row.date,
          until: row.until === row.date ? null : row.until,
          party: row.party == null ? null : (colors.get(row.party) ?? null),
          isCurrent: row.id === contest.id,
          isMinor: kept.size > 0 && row.party != null && !kept.has(row.id),
          winners: winners(row.id),
          runoff:
            row.runoff == null
              ? null
              : {
                  id: row.runoff,
                  path: slugs.get(row.runoff) ?? null,
                  date: row.runoffDate,
                  isCurrent: row.runoff === contest.id,
                  winners: winners(row.runoff),
                },
          local:
            local == null
              ? null
              : {
                  path: slugs.get(local) ?? null,
                  place: area.get(local) ?? "",
                  isCurrent: local === contest.id,
                },
        };
      };
      const hub = top == null ? null : (slugs.get(top) ?? null);
      return {
        general: general == null ? null : step(general),
        primaries: featured.map((row) => step(row)),
        others: Map.groupBy(others, (row) => row.party ?? "")
          .entries()
          .map(([party, rows]) => ({
            party: colors.get(party) ?? {
              id: party,
              label: "",
              short: null,
              color: null,
            },
            races: rows
              .map((row) => ({
                place: labels.full.get(row.district ?? "") ?? "",
                path: slugs.get(row.id) ?? null,
                isCurrent: row.id === contest.id,
              }))
              .toSorted((a, b) => a.place.localeCompare(b.place)),
          }))
          .toArray()
          .toSorted(
            (a, b) =>
              b.races.length - a.races.length ||
              a.party.label.localeCompare(b.party.label),
          ),
        day: {
          date: contest.date,
          path: hub,
          place: labels.full.get(topPolity ?? "") ?? "",
          part:
            election == null || election === top
              ? null
              : {
                  name: labels.full.get(election) ?? "",
                  path: slugs.get(election) ?? null,
                },
          groups: groups
            .filter(
              (row) =>
                row.kind !== "owner" || row.owner != null || row.count === 1,
            )
            .map((row) => {
              const isSingle = row.count === 1;
              const label =
                labels.full.get((isSingle ? row.sample : row.owner) ?? "") ??
                null;
              const year = contest.date?.slice(0, 4);
              const plain =
                isSingle && label != null && year != null
                  ? label.replace(
                      new RegExp(`^${year} (.+) election$`, "u"),
                      "$1",
                    )
                  : label;
              const court = isSingle
                ? (/\b([A-Z][a-z]+ Court (?:Judge|Justice)|Surrogate)\b/u.exec(
                    plain ?? "",
                  )?.[1] ?? null)
                : null;
              const county =
                court == null
                  ? ""
                  : (/(?:^|, )((?:New York|[A-Z][a-z]+) County)/u.exec(
                      plain ?? "",
                    )?.[1] ?? "");
              return { row, isSingle, label, plain, court, county };
            })
            .toSorted(
              (a, b) =>
                GROUPS.indexOf(
                  a.row.kind === "owner" && a.row.isPart ? "local" : a.row.kind,
                ) -
                  GROUPS.indexOf(
                    b.row.kind === "owner" && b.row.isPart
                      ? "local"
                      : b.row.kind,
                  ) ||
                Number(b.row.kind === "owner" && b.row.isPart) -
                  Number(a.row.kind === "owner" && a.row.isPart) ||
                Number(b.row.isHead) - Number(a.row.isHead) ||
                (a.court != null ? 2 : a.isSingle ? 0 : 1) -
                  (b.court != null ? 2 : b.isSingle ? 0 : 1) ||
                (a.court ?? "").localeCompare(b.court ?? "") ||
                a.county.localeCompare(b.county) ||
                Number((b.plain ?? "").startsWith(b.county)) -
                  Number((a.plain ?? "").startsWith(a.county)) ||
                b.row.count - a.row.count ||
                (a.plain ?? "").localeCompare(b.plain ?? "", undefined, {
                  numeric: true,
                }),
            )
            .map(({ row, isSingle, label, plain }) => ({
              kind: row.kind,
              label:
                isSingle &&
                !(
                  isScoped &&
                  row.owner != null &&
                  !(row.kind === "owner" && row.isPart)
                )
                  ? plain
                  : (labels.short.get(row.owner ?? "") ??
                    labels.full.get(row.owner ?? "") ??
                    label),
              count: row.count,
              path: isSingle
                ? (slugs.get(row.sample) ?? null)
                : row.kind === "owner" && row.isPart && election != null
                  ? (slugs.get(election) ?? hub)
                  : hub,
              anchor: isSingle
                ? null
                : label != null &&
                    (row.kind === "owner" ||
                      (isScoped && row.kind === "legislature"))
                  ? `race-${segment(label)}`
                  : isScoped
                    ? null
                    : "down-the-ballot",
            })),
        },
      };
    }),
  lists: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/lists",
        summary:
          "List a list contest's party lists, ranked for closed lists and by each candidate's votes for open lists, with each member's declared fate, the independents, and later list replacements",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          seats: v.number(),
          isRanked: v.boolean(),
          isOpen: v.boolean(),
          lists: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
              party: v.nullable(v.string()),
              votes: v.nullable(v.number()),
              seats: v.number(),
              members: v.array(
                v.object({
                  position: v.nullable(v.number()),
                  person: v.string(),
                  name: v.string(),
                  short: v.nullable(v.string()),
                  votes: v.nullable(v.number()),
                  fate: v.nullable(
                    v.picklist(["elected", "withdrew", "disqualified"]),
                  ),
                  note: v.nullable(v.string()),
                  elsewhere: v.nullable(Link),
                }),
              ),
            }),
          ),
          independents: v.array(
            v.object({
              person: v.string(),
              name: v.string(),
              isElected: v.boolean(),
            }),
          ),
          replacements: v.array(
            v.object({
              list: v.string(),
              date: v.nullable(v.string()),
              person: v.string(),
              name: v.string(),
              replaced: v.object({
                person: v.string(),
                name: v.string(),
                via: v.nullable(v.string()),
              }),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const day = contest.date ?? "infinity";
      const [closed, open] = await Promise.all([
        db.execute<{
          k: number;
          kind: string;
          party: string | null;
          alliance: string | null;
          label: string | null;
          color: string | null;
          seats: number | null;
          candidacy: string;
          person: string;
          role: string;
          position: number | null;
          fate: "elected" | "withdrew" | "disqualified" | null;
          note: string | null;
        }>(sql`
        select o.k, o.kind::text as kind, coalesce(pm.into_id, o.party_id) as party, o.alliance_id as alliance, o.ballot_label as label,
          case when o.party_id is not null then
            ${paint(sql`coalesce(pm.into_id, o.party_id)`, sql`${contest.election}::uuid`, sql`${day}::date`)}
          end as color,
          (select max(x.seats) from outcome x where x.result_set_id = rs.id and x.option_id = o.id and x.candidacy_id is null)::int as seats,
          cy.id as candidacy, coalesce(m.into_id, cy.person_id) as person, cy.role::text as role, cy.list_position as position,
          f.kind::text as fate, f.notes as note
        from contest_option o
        join candidacy cy on cy.option_id = o.id
          and ((o.kind = 'list' and cy.role = 'list_member' and (cy.list_position is not null or exists (
            select from outcome x join result_set r on r.id = x.result_set_id and ${latest("r")}
            where x.candidacy_id = cy.id and x.kind = 'elected'
          ))) or (o.kind in ('others', 'independents') and cy.role = 'head'))
        left join entity_merge m on m.entity_id = cy.person_id
        left join entity_merge pm on pm.entity_id = o.party_id
        left join lateral (
          select rs.id from result_set rs
          where rs.contest_id = o.contest_id and ${latest("rs")}
          order by exists (select from outcome x where x.result_set_id = rs.id) desc, array_position(${STAGES}, rs.stage)
          limit 1
        ) rs on true
        left join lateral (
          select x.kind, x.notes from outcome x
          where x.result_set_id = rs.id and x.candidacy_id = cy.id and x.kind in ('elected', 'withdrew', 'disqualified')
          order by x.kind = 'elected' desc limit 1
        ) f on true
        where o.contest_id = ${contest.id}::uuid
      `),
        db.execute<{
          list: number;
          party: string | null;
          alliance: string | null;
          label: string | null;
          color: string | null;
          seats: number | null;
          k: number;
          person: string | null;
          own: string | null;
          fate: "elected" | "withdrew" | "disqualified" | null;
          note: string | null;
        }>(sql`
          select l.k as list, coalesce(lm.into_id, l.party_id) as party, l.alliance_id as alliance, l.ballot_label as label,
            case when l.party_id is not null then
              ${paint(sql`coalesce(lm.into_id, l.party_id)`, sql`${contest.election}::uuid`, sql`${day}::date`)}
            end as color,
            (select max(x.seats) from outcome x join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
             where x.option_id = l.id and x.candidacy_id is null)::int as seats,
            o.k, coalesce(m.into_id, cy.person_id) as person, coalesce(om.into_id, o.party_id) as own,
            f.kind::text as fate, f.notes as note
          from contest_option l
          join contest_option o on o.parent_option_id = l.id
          left join candidacy cy on cy.option_id = o.id and cy.role in ('head', 'list_member') and o.kind = 'candidate'
          left join entity_merge lm on lm.entity_id = l.party_id
          left join entity_merge om on om.entity_id = o.party_id
          left join entity_merge m on m.entity_id = cy.person_id
          left join lateral (
            select x.kind, x.notes from outcome x join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
            where x.option_id = o.id and x.kind in ('elected', 'withdrew', 'disqualified')
            order by x.kind = 'elected' desc limit 1
          ) f on cy.id is not null
          where l.contest_id = ${contest.id}::uuid and l.kind = 'list'
        `),
      ]);
      const isOpen =
        closed.rows.every((row) => row.kind !== "list") &&
        open.rows.some((row) => row.person != null);
      if (closed.rows.every((row) => row.kind !== "list") && !isOpen) {
        return null;
      }
      const found = {
        rows: [
          ...closed.rows.map((row) => ({ ...row, own: null, option: null })),
          ...(isOpen ? open.rows : []).flatMap((row) =>
            row.person == null
              ? []
              : [
                  {
                    ...row,
                    k: row.list,
                    kind: "list",
                    person: row.person,
                    candidacy: "",
                    role: "head",
                    position: null,
                    option: row.k,
                  },
                ],
          ),
        ],
      };
      const people = [...new Set(found.rows.map((row) => row.person))];
      const [votes, elsewhere, replaced] = await Promise.all([
        db.execute<{ k: number; votes: number }>(sql`
          with chosen as (
            select rs.k from result_set rs
            where rs.contest_id = ${contest.id}::uuid and ${latest("rs")} and exists (
              select from result_rollup r where r.result_set_k = rs.k and r.area_k = ${contest.place?.k ?? 0} and r.measure = any(${COUNTED}) offset 0
            )
            order by array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
            limit 1
          ),
          picked as (
            select r.option_k, r.measure, r.count_round, r.value
            from chosen
            cross join lateral (
              select r.option_k, r.measure, r.count_round, r.value from result_rollup r
              where r.result_set_k = chosen.k and r.area_k = ${contest.place?.k ?? 0} and r.measure = any(${COUNTED}) offset 0
            ) r
          ),
          main as (
            select (${COUNTED})[min(array_position(${COUNTED}, measure))] as measure, min(count_round) as round from picked
          )
          select p.option_k as k, sum(p.value)::float8 as votes
          from picked p join main on main.measure = p.measure and main.round = p.count_round
          group by p.option_k
        `),
        db.execute<{ person: string; contest: string }>(sql`
          select distinct on (coalesce(m.into_id, cy.person_id)) coalesce(m.into_id, cy.person_id) as person, c.id as contest
          from candidacy cy
          left join entity_merge m on m.entity_id = cy.person_id
          join contest_option o on o.id = cy.option_id
          join contest c on c.id = o.contest_id
          join result_set rs on rs.contest_id = c.id and ${latest("rs")}
          join outcome x on x.result_set_id = rs.id and x.option_id = o.id and x.kind in ('elected', 'won')
            and (x.candidacy_id = cy.id or (x.candidacy_id is null and cy.role = 'head'))
          where c.election_id = ${contest.election}::uuid and c.id <> ${contest.id}::uuid
            and coalesce(m.into_id, cy.person_id) = any(${sql.param(people)}::uuid[])
          order by coalesce(m.into_id, cy.person_id), c.id
        `),
        db.execute<{
          person: string;
          date: string | null;
          predecessor: string;
          via: string | null;
        }>(sql`
          select coalesce(m.into_id, t.person_id) as person, t.from_date::text as date,
            coalesce(pm.into_id, p.person_id) as predecessor, p.ended_via::text as via
          from tenure t
          join tenure p on p.id = t.succeeds_tenure_id
          left join entity_merge m on m.entity_id = t.person_id
          left join entity_merge pm on pm.entity_id = p.person_id
          where t.contest_id = ${contest.id}::uuid and t.acquired_via = 'list_replacement'
          order by t.from_date
        `),
      ]);
      const seated = new Map(
        elsewhere.rows.map((row) => [row.person, row.contest]),
      );
      const [labels, slugs] = await Promise.all([
        names(
          [
            ...people,
            ...seated.values(),
            ...replaced.rows.map((row) => row.person),
            ...found.rows.flatMap((row) =>
              [row.party, row.alliance, row.own].filter((id) => id != null),
            ),
          ],
          contest.date,
        ),
        paths(seated.values().toArray()),
      ]);
      const tallies = new Map(votes.rows.map((row) => [row.k, row.votes]));
      const sums = new Map<number, number>();
      for (const row of open.rows) {
        sums.set(
          row.list,
          (sums.get(row.list) ?? 0) + (tallies.get(row.k) ?? 0),
        );
      }
      const lists = Map.groupBy(
        found.rows.filter((row) => row.kind === "list"),
        (row) => row.k,
      )
        .entries()
        .map(([k, members]) => {
          const head = members[0];
          const party = head?.party ?? null;
          const owner = party ?? head?.alliance ?? "";
          return {
            key: String(k),
            label:
              labels.full.get(owner) ??
              (head?.label === "" ? null : head?.label) ??
              labels.short.get(owner) ??
              "",
            short: labels.short.get(owner) ?? null,
            color: head?.color ?? null,
            party,
            votes: tallies.get(k) ?? sums.get(k) ?? null,
            seats:
              head?.seats ??
              members.filter((row) => row.fate === "elected").length,
            members: members
              .map((row) => {
                const other =
                  row.fate === "elected" ? null : seated.get(row.person);
                return {
                  position: row.position,
                  person: row.person,
                  name: labels.full.get(row.person) ?? "",
                  short:
                    row.own == null || row.own === party
                      ? null
                      : (labels.short.get(row.own) ?? null),
                  votes:
                    row.option == null ? null : (tallies.get(row.option) ?? 0),
                  fate: row.fate,
                  note: row.note,
                  elsewhere:
                    other == null
                      ? null
                      : {
                          name: labels.full.get(other) ?? "",
                          path: slugs.get(other) ?? null,
                        },
                };
              })
              .toSorted((a, b) => {
                const round = (note: string | null) =>
                  Number(/round (\d+)/u.exec(note ?? "")?.[1] ?? Infinity);
                return (
                  (a.position ?? Infinity) - (b.position ?? Infinity) ||
                  (b.votes ?? 0) - (a.votes ?? 0) ||
                  round(a.note) - round(b.note) ||
                  a.name.localeCompare(b.name)
                );
              }),
          };
        })
        .toArray()
        .toSorted(
          (a, b) =>
            b.seats - a.seats ||
            (b.votes ?? 0) - (a.votes ?? 0) ||
            a.label.localeCompare(b.label),
        );
      const owners = new Map(
        lists.flatMap((list) =>
          list.members.map((member) => [member.person, list.key] as const),
        ),
      );
      const independents = found.rows
        .filter((row) => row.kind === "others" || row.kind === "independents")
        .map((row) => ({
          person: row.person,
          name: labels.full.get(row.person) ?? "",
          isElected: row.fate === "elected",
        }))
        .toSorted(
          (a, b) =>
            Number(b.isElected) - Number(a.isElected) ||
            a.name.localeCompare(b.name, undefined, { numeric: true }),
        );
      let seats = 0;
      for (const list of lists) {
        seats += list.seats;
      }
      for (const independent of independents) {
        seats += Number(independent.isElected);
      }
      return {
        seats,
        isRanked: found.rows.every(
          (row) => row.kind !== "list" || row.position != null,
        ),
        isOpen,
        lists,
        independents,
        replacements: replaced.rows.flatMap((row) => {
          const list = owners.get(row.predecessor);
          return list == null
            ? []
            : [
                {
                  list,
                  date: row.date,
                  person: row.person,
                  name: labels.full.get(row.person) ?? "",
                  replaced: {
                    person: row.predecessor,
                    name: labels.full.get(row.predecessor) ?? "",
                    via: row.via,
                  },
                },
              ];
        }),
      };
    }),
};

async function turnout(contest: string | null, ks: Array<number>) {
  if (contest == null || ks.length === 0) {
    return new Map<
      number,
      { registered: number; voters: number; reporting: number; of: number }
    >();
  }
  const found = await db.execute<{
    k: number;
    registered: number | null;
    voters: number | null;
    reporting: number;
    of: number;
  }>(sql`
    with recursive day as (
      select e.date_from, e.kind from contest c join election e on e.id = c.election_id where c.id = ${contest}::uuid
    ),
    sets as materialized (
      select rs.k, rs.election_id from result_set rs
      join election e on e.id = rs.election_id
      cross join day d
      where rs.contest_id is null and ${latest("rs")} and e.date_from = d.date_from and e.kind = d.kind
    ),
    facts as materialized (
      select s.k as set, s.election_id as election, ua.area_id as area, v.measure, sum(v.value)::float8 as value
      from sets s
      join result_value v on v.result_set_k = s.k and v.option_k = 0 and v.measure in (2, 3, 14) and v.count_round = 0
      join reporting_unit ru on ru.k = v.unit_k
      cross join lateral (
        select coalesce(ru.area_id, (
          select x.area_id from reporting_unit_area x where x.unit_id = ru.id
          order by x.valid @> (select date_from from day) desc limit 1
        )) as area_id
      ) ua
      where ua.area_id is not null
      group by 1, 2, 3, 4
    ),
    targets as (
      select a.k, a.id from area a where a.k = any(${sql.param(ks)}::int[])
    ),
    kinds as materialized (
      select distinct q.kind from sets s join election y on y.id = s.election_id join polity q on q.id = y.polity_id
    ),
    expected as materialized (
      select e.id as election, pt.area_id as area
      from election e
      join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
      join polity p on p.id = e.polity_id
      cross join day d
      where e.date_from = d.date_from and e.kind = d.kind and p.kind in (select kind from kinds)
    ),
    up(origin, id, depth) as (
      select distinct x.area, x.area, 0 from (select area from facts union select area from expected) x
      union all
      select up.origin, p.parent_id, up.depth + 1 from up
      join area_parent p on p.area_id = up.id and p.valid @> (select date_from from day)
      where up.depth < 5
    ),
    tallied as (
      select t.k, f.election, f.set, f.measure, sum(f.value) as value
      from targets t
      join up on up.id = t.id
      join facts f on f.area = up.origin
      group by 1, 2, 3, 4
    ),
    best as (
      select k, election, max(value) filter (where measure = 2) as registered,
        coalesce(max(value) filter (where measure = 14), max(value) filter (where measure = 3)) as voters
      from tallied group by 1, 2
    )
    select k,
      sum(registered) filter (where registered > 0 and voters <= registered) as registered,
      sum(voters) filter (where registered > 0 and voters <= registered) as voters,
      count(*) filter (where registered > 0 and voters <= registered)::int as reporting,
      greatest(count(*), (
        select count(distinct x.election) from targets t join up on up.id = t.id
        join expected x on x.area = up.origin
        where t.k = best.k and x.area <> t.id
      ))::int as of
    from best group by k
  `);
  return new Map(
    found.rows.flatMap((row) =>
      row.registered == null || row.voters == null
        ? []
        : [
            [
              row.k,
              {
                registered: row.registered,
                voters: row.voters,
                reporting: row.reporting,
                of: row.of,
              },
            ],
          ],
    ),
  );
}

async function anchors(ks: Array<number>, day: string) {
  const points = await db.execute<{ k: number; x: number; y: number }>(sql`
    select k, ST_X(p) as x, ST_Y(p) as y from (
      select distinct on (a.k) a.k,
        ${spot("coalesce(g.label_point, ST_PointOnSurface(g.geom))", "a.scheme_code")} as p
      from area a join area_geometry g on g.area_id = a.id
      where a.k = any(${sql.param(ks)}::int[]) and g.valid @> ${day}::date
      order by a.k
    ) x where p is not null
  `);
  return new Map(
    points.rows.map((row) => [row.k, [row.x, row.y] as [number, number]]),
  );
}

async function redrawn(
  ids: Array<string>,
  now: { id: string; date: string | null },
  then: { id: string; date: string | null } | null,
  scope: string | null = null,
) {
  if (then == null || ids.length === 0) {
    return new Map<number, number>();
  }
  const found = await db.execute<{ k: number; overlap: number }>(sql`
    with picked as (
      select a.id, a.k,
        coalesce((
          select bs.as_of from boundary_set bs
          where bs.scheme_code = a.scheme_code
            and bs.id = (select boundary_set_id from contest where id = ${now.id}::uuid)
        ), ${now.date ?? "infinity"}::date) as now_at,
        coalesce((
          select bs.as_of from boundary_set bs
          where bs.scheme_code = a.scheme_code
            and bs.id = (select boundary_set_id from contest where id = ${then.id}::uuid)
        ), ${then.date ?? "infinity"}::date) as then_at
      from area a where a.id = any(${sql.param([...ids, ...(scope == null ? [] : [scope])])}::uuid[])
    ),
    versions as (
      select p.id, p.k, g1.geom as a, g2.geom as b, g1.valid <> g2.valid as differs
      from picked p
      join area_geometry g1 on g1.area_id = p.id and g1.valid @> p.now_at
      join area_geometry g2 on g2.area_id = p.id and g2.valid @> p.then_at
    ),
    frame as (
      select a, b from versions where id = ${scope}::uuid and differs
    ),
    paired as (
      select v.k,
        case when f.a is null then v.a else ST_CollectionExtract(ST_Intersection(v.a, f.a, 0.00001), 3) end as a,
        case when f.a is null then v.b else ST_CollectionExtract(ST_Intersection(v.b, f.b, 0.00001), 3) end as b
      from versions v left join frame f on true
      where v.id = any(${sql.param(ids)}::uuid[]) and (v.differs or f.a is not null)
    )
    select k, (ST_Area(ST_Intersection(a, b, 0.00001)) / greatest(ST_Area(a), ST_Area(b)))::float8 as overlap
    from paired
    where greatest(ST_Area(a), ST_Area(b)) > 0
  `);
  return new Map(
    found.rows
      .filter((row) => row.overlap < 0.97)
      .map((row) => [row.k, Math.round(row.overlap * 1000) / 1000]),
  );
}

function share(
  entry:
    | { registered: number; voters: number; reporting: number; of: number }
    | undefined,
  valid: number,
) {
  return entry == null ||
    valid === 0 ||
    entry.voters < valid * (entry.reporting === entry.of ? 0.98 : 0.9)
    ? null
    : entry.voters / entry.registered;
}

function sane(
  entry:
    | { registered: number; voters: number; reporting?: number; of?: number }
    | null
    | undefined,
  valid: number,
) {
  return entry == null ||
    entry.voters <
      valid * ((entry.reporting ?? 0) === (entry.of ?? 0) ? 0.98 : 0.9)
    ? null
    : entry;
}

function merge<
  T extends { owner: string; from: string | null; to: string | null },
>(rows: Array<T>, days: number) {
  const merged: Array<T> = [];
  for (const row of rows) {
    const last = merged.at(-1);
    if (
      last?.owner === row.owner &&
      last.to != null &&
      row.from != null &&
      Date.parse(row.from) - Date.parse(last.to) < days * 864e5
    ) {
      last.to = row.to;
    } else {
      merged.push({ ...row });
    }
  }
  return merged;
}

async function poll(contest: string | null, k: number | null, isAsked = true) {
  if (contest == null || !isAsked) {
    return { turnout: null, quorum: null, ballot: null, isStv: false };
  }
  const found = await db.execute<{
    registered: number | null;
    voters: number | null;
    basis: "valid" | "rejected" | null;
    quorum: number | null;
    ballot: number | null;
    isStv: boolean;
  }>(sql`
    select t.*, ((coalesce(es.rules, '{}') || coalesce(c.rules_override, '{}')) ->> 'quorum')::float8 as quorum,
      (coalesce(es.rules, '{}') || coalesce(c.rules_override, '{}')) ->> 'formula' = 'stv' as "isStv",
      case (coalesce(es.rules, '{}') || coalesce(c.rules_override, '{}')) ->> 'formula'
        when 'block' then c.magnitude when 'limited' then c.magnitude - 1 end as ballot
    from (
      select nullif(sum(registered), 0)::float8 as registered,
        sum(coalesce(voters, valid + coalesce(invalid, 0)))::float8 as voters,
        case when bool_and(voters is not null) then null
          when bool_or(voters is null and invalid is not null) then 'rejected' else 'valid' end as basis
      from (
        select nullif(max(r.value) filter (where r.measure = 2), 0) as registered,
          coalesce(max(r.value) filter (where r.measure = 14), max(r.value) filter (where r.measure = 3)) as voters,
          max(r.value) filter (where r.measure = 4) as valid,
          max(r.value) filter (where r.measure = 5) as invalid
        from result_set rs cross join lateral (
          select x.channel, x.measure, x.value from result_rollup x
          where x.result_set_k = rs.k and x.area_k = ${k}::int and x.option_k = 0
            and x.measure in (2, 3, 4, 5, 14) and x.count_round = 0 offset 0
        ) r
        where rs.contest_id = ${contest}::uuid and ${latest("rs")}
        group by r.channel
      ) c
    ) t
    join contest c on c.id = ${contest}::uuid
    left join electoral_system es on es.id = c.electoral_system_id
  `);
  const row = found.rows[0];
  return {
    turnout:
      row?.registered == null ||
      row.voters == null ||
      (row.basis != null && row.voters > row.registered)
        ? null
        : { registered: row.registered, voters: row.voters, basis: row.basis },
    quorum: row?.quorum ?? null,
    ballot: row?.ballot != null && row.ballot > 1 ? row.ballot : null,
    isStv: row?.isStv ?? false,
  };
}

async function explain(
  contest: { id: string; date: string | null },
  k: number | null,
) {
  if (k == null) {
    return [];
  }
  const found = await db.execute<{
    key: string;
    party: string | null;
    label: string | null;
    votes: number;
    seats: number;
    note: string | null;
    person: string | null;
    joined: string | null;
    rules: Record<string, unknown>;
    system: string | null;
    rule: string | null;
  }>(sql`
    with own as (
      select c.id, c.election_id, c.electoral_system_id as system,
        coalesce(es.rules, '{}') || coalesce(c.rules_override, '{}') as rules
      from contest c left join electoral_system es on es.id = c.electoral_system_id
      where c.id = ${contest.id}::uuid
    ),
    chosen as (
      select rs.id, rs.k from result_set rs
      where rs.contest_id = ${contest.id}::uuid and ${latest("rs")}
      order by exists (select from outcome x where x.result_set_id = rs.id) desc, array_position(${STAGES}, rs.stage)
      limit 1
    ),
    tallied as (
      select r.option_k, sum(r.value)::float8 as votes
      from chosen cross join lateral (
        select x.option_k, x.value, x.count_round from result_rollup x
        where x.result_set_k = chosen.k and x.area_k = ${k}::int and x.measure = 1 and x.option_k is not null offset 0
      ) r
      where r.count_round = (
        select min(y.count_round) from result_rollup y
        where y.result_set_k = chosen.k and y.area_k = ${k}::int and y.measure = 1
      )
      group by r.option_k
    )
    select o.id as key, coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label, t.votes,
      coalesce((
        select greatest(coalesce(sum(x.seats) filter (where x.candidacy_id is null), 0),
          count(*) filter (where x.candidacy_id is not null and x.kind = 'elected'),
          case when bool_or(x.kind = 'won' and x.candidacy_id is null) then 1 else 0 end)
        from outcome x join chosen on chosen.id = x.result_set_id
        where x.option_id = o.id and x.kind in ('won', 'elected')
      ), 0)::int as seats,
      (select x.notes from outcome x join chosen on chosen.id = x.result_set_id
       where x.option_id = o.id and x.kind in ('won', 'elected') and x.notes is not null and x.notes not like 'Derived:%'
       limit 1) as note,
      i.person, i.joined, own.rules,
      (select n.text from name n where n.entity_id = own.system order by n.is_preferred desc limit 1) as system,
      (select n.body_md from note n where n.entity_id = own.system and n.kind = 'methodology' limit 1) as rule
    from own
    join contest_option o on o.contest_id = own.id
    join tallied t on t.option_k = o.k
    left join entity_merge pm on pm.entity_id = o.party_id
    left join lateral (
      select coalesce(m.into_id, cy.person_id) as person, (
          select lower(pp.valid)::text from person_party_membership pp
          where pp.person_id = coalesce(m.into_id, cy.person_id) and pp.party_id = coalesce(pm.into_id, o.party_id)
            and lower(pp.valid) > ${contest.date ?? null}::date and lower(pp.valid) < ${contest.date ?? null}::date + 366
          order by lower(pp.valid) limit 1
        ) as joined
      from candidacy cy
      join chosen on true
      join outcome x on x.result_set_id = chosen.id and x.candidacy_id = cy.id and x.kind = 'elected'
      left join entity_merge m on m.entity_id = cy.person_id
      where cy.option_id = o.id and cy.is_independent
      limit 1
    ) i on true
  `);
  const rows = found.rows;
  const rules = rows[0]?.rules ?? {};
  const thresholds = [rules.threshold ?? []]
    .flat()
    .filter(
      (entry): entry is { percent: number; scope?: string; level?: string } =>
        typeof entry === "object" &&
        entry != null &&
        "percent" in entry &&
        "scope" in entry &&
        entry.scope === "national" &&
        (!("level" in entry) ||
          entry.level === "party" ||
          entry.level === "list"),
    );
  const access =
    typeof rules.remainderAccess === "object" && rules.remainderAccess != null
      ? (rules.remainderAccess as { quotas?: number; votes?: number })
      : null;
  const shares = new Map<string, number>();
  if (thresholds.length > 0) {
    const national = await db.execute<{ party: string; share: number }>(sql`
      with siblings as (
        select c.id, a.k from contest c
        join contest own on own.id = ${contest.id}::uuid
        join area a on a.id = c.district_area_id
        where c.election_id = own.election_id and c.electoral_system_id is not distinct from own.electoral_system_id
      ),
      votes as (
        select coalesce(pm.into_id, o.party_id) as party, r.value
        from siblings s
        join result_set rs on rs.contest_id = s.id and ${latest("rs")}
        cross join lateral (
          select x.option_k, x.value from result_rollup x
          where x.result_set_k = rs.k and x.area_k = s.k and x.measure = 1 and x.count_round = 0 and x.option_k is not null offset 0
        ) r
        join contest_option o on o.k = r.option_k
        left join entity_merge pm on pm.entity_id = o.party_id
      )
      select party, (sum(value) / nullif((select sum(value) from votes), 0))::float8 as share
      from votes where party is not null group by party
    `);
    for (const row of national.rows) {
      shares.set(row.party, row.share);
    }
  }
  const people = rows.flatMap((row) =>
    row.person == null ? [] : [row.person],
  );
  const parties = rows.flatMap((row) => (row.party == null ? [] : [row.party]));
  const labels = await names([...people, ...parties], contest.date);
  const label = (row: (typeof rows)[number]) =>
    labels.short.get(row.party ?? "") ??
    labels.full.get(row.party ?? "") ??
    row.label ??
    "";
  const explained = [];
  for (const row of rows) {
    const beaten = rows.filter(
      (other) => other.votes > row.votes && other.seats < row.seats,
    );
    if (row.seats === 0 || beaten.length === 0) {
      continue;
    }
    const threshold = thresholds.find((entry) =>
      beaten.every(
        (other) =>
          other.party != null &&
          shares.has(other.party) &&
          (shares.get(other.party) ?? 0) * 100 < entry.percent,
      ),
    );
    const isShut =
      access != null &&
      beaten.every(
        (other) =>
          (access.quotas != null && other.seats === 0) ||
          (access.votes != null && other.votes < access.votes),
      );
    const kind:
      | "note"
      | "access"
      | "remainder"
      | "compensation"
      | "threshold"
      | "pooling"
      | "bonus"
      | "independent"
      | null =
      row.note != null
        ? "note"
        : isShut
          ? "access"
          : rules.remainders === "national"
            ? "remainder"
            : rules.compensation === "constituency_seats"
              ? "compensation"
              : threshold != null
                ? "threshold"
                : rules.pooling != null
                  ? "pooling"
                  : rules.bonus != null
                    ? "bonus"
                    : row.person != null
                      ? "independent"
                      : null;
    if (kind == null) {
      continue;
    }
    const merged = explained.find(
      (entry) =>
        (kind === "threshold" || kind === "access") && entry.kind === kind,
    );
    if (merged != null) {
      for (const other of beaten.map(label)) {
        if (!merged.beaten.includes(other)) {
          merged.beaten.push(other);
        }
      }
      continue;
    }
    explained.push({
      key: row.key,
      kind,
      label: label(row),
      note: kind === "note" ? row.note : null,
      percent: kind === "threshold" ? (threshold?.percent ?? null) : null,
      votes: kind === "access" ? (access?.votes ?? null) : null,
      beaten:
        kind === "threshold" || kind === "access" ? beaten.map(label) : [],
      person: row.person == null ? null : (labels.full.get(row.person) ?? null),
      joined: row.joined,
      system: row.system,
      rule: row.rule,
    });
  }
  return explained;
}

async function attendance(
  known: {
    registered: number;
    voters: number;
    basis: "valid" | "rejected" | null;
  } | null,
  contest: string,
  k: number,
  valid: number,
  before: string | null,
  prior: number | null,
  district: string | null,
  area: string,
  date: string | null,
) {
  const [pooled, earlier, polled, inside] = await Promise.all([
    known == null ? turnout(contest, [k]) : null,
    turnout(prior == null ? null : before, [k]),
    prior == null ? null : poll(before, k),
    known != null || district == null
      ? null
      : db.execute<{ whole: boolean | null }>(sql`
          with recursive up(id, depth) as (
            select ${area}::uuid, 0
            union all
            select p.parent_id, up.depth + 1 from area_parent p join up on p.area_id = up.id where up.depth < 8
          )
          select case when exists (select from up where up.id = ${district}::uuid) then true else (
            select ST_Area(ST_Intersection(a.geom, d.geom)) >= 0.98 * ST_Area(a.geom)
            from (
              select geom from area_geometry where area_id = ${area}::uuid
              order by valid @> ${date ?? "infinity"}::date desc, upper(valid) desc nulls first limit 1
            ) a
            cross join (
              select geom from area_geometry where area_id = ${district}::uuid
              order by valid @> ${date ?? "infinity"}::date desc, upper(valid) desc nulls first limit 1
            ) d
          ) end as whole
        `),
  ]);
  const turned =
    inside?.rows[0]?.whole === false
      ? null
      : sane(known ?? pooled?.get(k), valid);
  return turned == null
    ? null
    : {
        registered: turned.registered,
        voters: turned.voters,
        reporting: turned.reporting,
        of: turned.of,
        basis: known?.basis ?? null,
        earlier:
          sane(earlier.get(k), prior ?? 0) ?? sane(polled?.turnout, prior ?? 0),
      };
}

async function universe(
  contest: string,
  level: string,
  area: string,
  day: string,
) {
  const found = await db.execute<{
    id: string;
    k: number;
    held: boolean;
    outside: boolean;
    pending: boolean;
  }>(sql`
    with recursive base as (
      select c.office_id, c.body_id, c.party_scope_id, ec.date_from, ec.date_to
      from contest c join election ec on ec.id = c.election_id
      where c.id = ${contest}::uuid and (c.office_id is not null or c.body_id is not null)
    ),
    peers as (
      select r.district_area_id, r.election_id, r.party_scope_id
      from base b join contest r on b.office_id is null and r.body_id = b.body_id
      union all
      select r.district_area_id, r.election_id, r.party_scope_id
      from base b join contest r on r.office_id = b.office_id
      union all
      select r.district_area_id, r.election_id, r.party_scope_id
      from base b join contest p on p.office_id = b.office_id join contest r on r.allocation_parent_id = p.id
    ),
    candidate as (
      select a.id, a.k,
        bool_or(er.date_from between b.date_from and coalesce(b.date_to, b.date_from)) as held
      from base b
      cross join peers r
      join area a on a.id = r.district_area_id and a.scheme_code = ${level}
      join election er on er.id = r.election_id
      where (r.party_scope_id is null or r.party_scope_id = b.party_scope_id)
        and exists (select from area_geometry g where g.area_id = a.id and g.valid @> ${day}::date)
      group by a.id, a.k
    ),
    up(start, id, depth) as (
      select id, id, 0 from candidate
      union all
      select up.start, p.parent_id, up.depth + 1 from area_parent p join up on p.area_id = up.id
      where p.valid @> ${day}::date and up.depth < 4
    ),
    down(id, scheme, depth) as (
      select ${area}::uuid, ''::text, 0
      union all
      select a.id, a.scheme_code, down.depth + 1 from area_parent p join down on p.parent_id = down.id
      join area a on a.id = p.area_id
      where p.valid @> ${day}::date and down.depth < 4 and down.scheme <> ${level} and not exists (select from candidate)
    ),
    inside(id, scheme, depth) as (
      select ${area}::uuid, ''::text, 0 where exists (select from candidate)
      union all
      select a.id, a.scheme_code, inside.depth + 1 from inside
      join area_parent p on p.parent_id = inside.id and p.valid @> ${day}::date
      join area a on a.id = p.area_id
      where inside.depth < 2 and inside.scheme <> ${level} and (inside.depth = 0 or a.scheme_code = ${level})
    )
    select distinct candidate.id, candidate.k, candidate.held, false as outside,
      not candidate.held and (select b.party_scope_id is not null from base b) as pending
    from candidate
    join up on up.start = candidate.id where up.id = ${area}::uuid
    union
    select a.id, a.k, true, false, false from down
    join area a on a.id = down.id and a.scheme_code = ${level}
    where down.depth > 0 and exists (select from area_geometry g where g.area_id = a.id and g.valid @> ${day}::date)
    union
    select a.id, a.k, false, true, false from inside
    join area a on a.id = inside.id and a.scheme_code = ${level}
    where inside.depth > 0 and not exists (select from candidate c where c.id = a.id)
      and exists (select from area_geometry g where g.area_id = a.id and g.valid @> ${day}::date)
  `);
  return found.rows;
}

async function framing(area: string, day: string, ks: Array<number> = []) {
  const found = await db.execute<{
    west: number;
    south: number;
    east: number;
    north: number;
  }>(sql`
    with shapes as (
      select a.id, a.scheme_code as scheme, g.valid, g.geom
      from area a
      join area_geometry g on g.area_id = a.id and g.valid @> ${day}::date
      where ${ks.length === 0 ? sql`a.id = ${area}::uuid` : sql`a.k = any(${sql.param(ks)}::int[])`}
    ),
    bounds as (
      select box, least(greatest(ST_XMax(box) - ST_XMin(box), ST_YMax(box) - ST_YMin(box)) / 500, 0.01) as t
      from (select ST_SetSRID(ST_Extent(geom)::geometry, 4326) as box from shapes) e
      where (ST_XMax(box) - ST_XMin(box)) * (ST_YMax(box) - ST_YMin(box)) <= 4
    ),
    water as (
      select ST_Subdivide(ST_Difference(b.box, ST_Difference(
        coalesce((select ST_UnaryUnion(ST_Collect(ST_MakeValid(ST_Simplify(ST_ClipByBox2D(c.geom, b.box), b.t, true))))
          from display.land c where c.geom && b.box), ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)),
        coalesce((select ST_UnaryUnion(ST_Collect(ST_MakeValid(ST_Simplify(ST_ClipByBox2D(l.geom, b.box), b.t, true))))
          from display.lake l where l.geom && b.box), ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)))), 64) as geom
      from bounds b
    ),
    dry as (
      select s.id, s.scheme, coalesce(
        (select coalesce(c.geom, s.geom) from display.clipped c join display.meta dm on dm.mask_version = c.mask_version
          where c.area_id = s.id and c.valid = s.valid and c.geom_hash = md5(ST_AsBinary(s.geom))),
        (select case when ST_IsEmpty(w.d) then null else w.d end from (
          select ST_Multi(ST_CollectionExtract(ST_Difference(ST_MakeValid(ST_Simplify(s.geom, b.t, true)),
            (select ST_UnaryUnion(ST_Collect(x.geom)) from water x where x.geom && s.geom)), 3)) as d
          from bounds b
          where exists (select from water x where x.geom && s.geom and ST_Intersects(x.geom, s.geom))
        ) w),
        s.geom) as geom
      from shapes s
    ),
    parts as (
      select w.id, ST_Simplify(d.geom, least(greatest(ST_XMax(d.geom) - ST_XMin(d.geom), ST_YMax(d.geom) - ST_YMin(d.geom)) / 500, 0.01), true) as geom, w.scheme
      from dry w
      cross join lateral ST_Dump(w.geom) d
    ),
    ranked as (
      select parts.*, row_number() over (partition by id order by ST_Area(geom) desc) = 1 as anchor
      from parts
    ),
    anchors as materialized (
      select id, ST_Area(geom) as area, ST_ConvexHull(geom) as hull from ranked where anchor
    ),
    kept as (
      select p.geom, p.scheme from ranked p
      join anchors x on x.id = p.id
      where ST_Area(p.geom) >= x.area * 0.05 or ST_DWithin(p.geom, x.hull, 2)
    ),
    framed as (
      select ST_Transform(ST_SetSRID(ST_Extent(${plane("geom", "scheme")})::geometry, 3857), 4326) as box
      from kept
    )
    select ST_XMin(box) as west, ST_YMin(box) as south, ST_XMax(box) as east, ST_YMax(box) as north
    from framed where box is not null
  `);
  return found.rows[0] ?? null;
}

async function sketch(
  area: { id: string; k: number },
  level: string,
  day: string,
  ks: Array<number> = [],
  inner: Array<number> = [],
  contest: string | null = null,
) {
  const whole =
    contest == null ? [] : await universe(contest, level, area.id, day);
  const counted = new Set(ks);
  const absent = whole.filter((row) => !counted.has(row.k));
  const offside = await outside(
    level,
    area.id,
    [...ks, ...whole.map((row) => row.k)],
    day,
  );
  const off = new Set(offside.map((entry) => entry.k));
  const [frame, found, nested, labels, outlying] = await Promise.all([
    framing(
      area.id,
      day,
      [
        ...ks,
        ...whole.filter((row) => !row.outside).map((row) => row.k),
      ].filter((k) => !off.has(k)),
    ),
    db.execute<{
      scheme: string;
      tiles: string;
      approximate: Array<number>;
      shaped: number;
      credits: Array<string>;
    }>(sql`
      select distinct on (bs.scheme_code) bs.scheme_code as scheme,
        coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles,
        (
          select count(distinct x.k)::int from area x join area_geometry g on g.area_id = x.id
          where x.k = any(${sql.param(ks)}::int[]) and g.valid @> coalesce(bs.as_of, ${day}::date)
        ) as shaped,
        array(
          select distinct c.credit from area x
          join area_geometry g on g.area_id = x.id and g.valid @> coalesce(bs.as_of, ${day}::date)
          join source s on s.id = g.source_id
          cross join lateral (values (s.publisher), (nullif(s.attribution, ''))) c(credit)
          where x.scheme_code = bs.scheme_code and x.k = any(${sql.param([...ks, area.k])}::int[]) and c.credit is not null
        ) as credits,
        array(
          select distinct x.k from (select area_id, valid from area_geometry where quality = 'synthetic' offset 0) g
          join area x on x.id = g.area_id
          where x.scheme_code = bs.scheme_code and g.valid @> coalesce(bs.as_of, ${day}::date)
            and x.k = any(${sql.param([...ks, ...absent.map((row) => row.k)])}::int[])
        ) as approximate
      from boundary_set bs
      where bs.scheme_code in (${level}, (select scheme_code from area where id = ${area.id}::uuid))
        and bs.valid @> ${day}::date
      order by bs.scheme_code, bs.valid_from desc nulls last
    `),
    db.execute<{ tiles: string }>(sql`
      select coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles
      from boundary_set bs
      where bs.scheme_code = (select scheme_code from area where k = ${inner[0] ?? -1})
        and bs.valid @> ${day}::date
      order by bs.valid_from desc nulls last
      limit 1
    `),
    names(
      absent.map((row) => row.id),
      day,
    ),
    level === "us.state"
      ? db.execute<{ k: number; code: string }>(sql`
        select a.k, (array['AS', 'GU', 'MP', 'PR', 'VI'])[array_position(array['60', '66', '69', '72', '78'], c.code)] as code from area a join area_code c on c.area_id = a.id
        where a.k = any(${sql.param([...ks, ...absent.map((row) => row.k)])}::int[])
          and c.code in ('60', '66', '69', '72', '78')
      `)
      : null,
  ]);
  const inside = stored(nested.rows[0]?.tiles);
  const tiled = found.rows.find((row) => row.scheme === level);
  const own = found.rows.find(
    (row) =>
      row.scheme !== level &&
      stored(row.tiles) != null &&
      projectionOf(row.scheme) === projectionOf(level),
  );
  const [split, ownSplit] = await Promise.all([
    parted(level, area.id, day),
    own == null ? undefined : parted(own.scheme, area.id, day),
  ]);
  const strip = [
    ...(outlying?.rows ?? []).map((entry) => ({
      ...entry,
      group: "territory" as const,
    })),
    ...offside,
  ];
  return tiled == null || frame == null
    ? null
    : {
        tiles: stored(tiled.tiles),
        own:
          own == null
            ? null
            : { tiles: own.tiles, k: area.k, parted: ownSplit },
        parted: split,
        nested: inside == null ? null : { tiles: inside, ks: inner },
        frame,
        blank: absent
          .map((row) => ({
            k: row.k,
            name: labels.full.get(row.id) ?? "",
            isHeld: row.held,
            isOutside: row.outside,
            isPending: row.pending,
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
        approximate: tiled.approximate,
        ...(strip.length > 0 && { outlying: strip }),
        shaped: ks.length === 0 ? null : tiled.shaped,
        credits:
          ks.length === 0
            ? null
            : [...new Set([...tiled.credits, ...(own?.credits ?? [])])],
      };
}

export async function decide(ids: Array<string>) {
  if (ids.length === 0) {
    return new Map<
      string,
      {
        first: string;
        final: string;
        isPrimary: boolean;
        next: { id: string; date: string; isGeneral: boolean } | null;
      }
    >();
  }
  const found = await db.execute<{
    id: string;
    first: string;
    final: string;
    isPrimary: boolean;
    next: string | null;
    date: string | null;
    isGeneral: boolean | null;
  }>(sql`
    with recursive ids(id) as (
      select distinct x.id from unnest(${sql.param(ids)}::uuid[]) as x(id)
    ),
    back(root, id, depth) as (
      select id, id, 0 from ids
      union all
      select back.root, c.previous_round_id, back.depth + 1 from back join contest c on c.id = back.id
      where c.previous_round_id is not null and back.depth < 8
    ),
    ahead(root, id, depth, held, date, kind) as (
      select id, id, 0, true, null::date, null::text from ids
      union all
      select ahead.root, n.id, ahead.depth + 1, n.held, n.date, n.kind from ahead
      cross join lateral (
        select n.id, ne.status = 'held' and exists (
            select from result_set rs where rs.contest_id = n.id and ${latest("rs")}
          ) as held,
          ne.date_from as date, ne.kind::text as kind
        from contest n join election ne on ne.id = n.election_id
        where n.previous_round_id = ahead.id and ne.status::text not in ('canceled', 'annulled')
        order by ne.date_from, n.round_number desc, n.id limit 1
      ) n
      where ahead.held and ahead.depth < 8
    ),
    ends as (
      select i.id,
        (select b.id from back b where b.root = i.id order by b.depth desc limit 1) as first,
        (select a.id from ahead a where a.root = i.id and a.held order by a.depth desc limit 1) as final,
        (select a.id from ahead a where a.root = i.id and not a.held limit 1) as next,
        (select a.date::text from ahead a where a.root = i.id and not a.held limit 1) as date,
        (select a.kind in ('general', 'local') from ahead a where a.root = i.id and not a.held limit 1) as "isGeneral"
      from ids i
    )
    select ends.*, coalesce((
      select fe.kind = 'primary' and le.kind in ('general', 'local')
      from contest f join election fe on fe.id = f.election_id, contest l join election le on le.id = l.election_id
      where f.id = ends.first and l.id = ends.final
    ), false) as "isPrimary"
    from ends
  `);
  return new Map(
    found.rows.map((row) => [
      row.id,
      {
        first: row.first,
        final: row.final,
        isPrimary: row.isPrimary,
        next:
          row.next == null
            ? null
            : {
                id: row.next,
                date: row.date ?? "",
                isGeneral: row.isGeneral === true,
              },
      },
    ]),
  );
}

async function sequel(
  group: Array<{ isRunoff: boolean; runoffs: Array<string> }>,
  isBrief: boolean,
  area: number,
  lineage: Array<Relation>,
) {
  const head = !isBrief && group.length === 1 ? group[0] : undefined;
  const id = head?.isRunoff === true ? undefined : head?.runoffs[0];
  if (id == null) {
    return null;
  }
  const [meta, linked] = await Promise.all([
    db.execute<{ date: string }>(sql`
      select e.date_from::text as date from contest c join election e on e.id = c.election_id
      where c.id = ${id}::uuid
    `),
    paths([id]),
  ]);
  const when = meta.rows[0]?.date ?? null;
  const all = await load([id], [area], when ?? "infinity", [id]);
  const loaded = all.get(area);
  const [lead, runner] = (
    loaded == null
      ? []
      : compare(loaded.side, null, lineage, "party").flatMap((row) =>
          row.current == null ? [] : [row.current],
        )
  ).toSorted((a, b) => b.votes - a.votes);
  if (lead == null) {
    return null;
  }
  const people = await names(
    [lead.person, runner?.person].filter((person) => person != null),
    when,
  );
  return {
    path: linked.get(id) ?? null,
    date: when,
    label: people.full.get(lead.person ?? "") ?? "",
    short: people.short.get(lead.person ?? "") ?? null,
    runner: people.full.get(runner?.person ?? "") ?? null,
    runnerShort: people.short.get(runner?.person ?? "") ?? null,
    share: lead.share,
    margin: lead.share - (runner?.share ?? 0),
    isWon: lead.outcome === "elected" || lead.outcome === "won",
  };
}

function reckon(
  side: Side | undefined,
  relations: Array<Relation>,
  grouping: Grouping,
  isPooled: boolean,
) {
  if (side == null) {
    return [];
  }
  return compare(
    side,
    null,
    relations,
    "party",
    isPooled || grouping === "alliance" ? grouping : "option",
  ).flatMap((row) =>
    row.current == null ? [] : [{ ...row, cell: row.current }],
  );
}

function carried(
  rows: Array<{
    party: string | null;
    seats: number;
    size?: number | null;
  }> | null,
  filled: number,
  parentOf: Map<string, string>,
) {
  if (rows == null) {
    return null;
  }
  let earlier = 0;
  for (const row of rows) {
    earlier += row.seats;
  }
  const expected = rows[0]?.size ?? filled;
  if (earlier < expected * 0.9 || earlier > expected * 1.1) {
    return null;
  }
  return Map.groupBy([...rows, { party: null, seats: 0 }], (row) =>
    row.party == null
      ? "independent"
      : `party:${parentOf.get(row.party) ?? row.party}`,
  )
    .entries()
    .map(([key, group]) => {
      let seats = 0;
      for (const row of group) {
        seats += row.seats;
      }
      return { key, seats };
    })
    .toArray();
}

function naming(
  head:
    | {
        id: string;
        owner: string | null;
        office: string | null;
        elects: string | null;
        party: string | null;
        isOffice: boolean;
      }
    | undefined,
  single: { id: string } | null,
  groups: Array<
    Array<{ owner: string | null; office: string | null; party: string | null }>
  >,
) {
  const titled =
    head?.isOffice === true ? head.office : (head?.elects ?? head?.owner);
  const isShared =
    head != null &&
    groups.filter((other) =>
      head.isOffice
        ? other[0]?.office === head.office && other[0].party === head.party
        : other[0]?.owner === head.owner && other[0].party === head.party,
    ).length > 1;
  const isTitled =
    single == null ||
    ((head?.party != null || head?.isOffice === true) && !isShared);
  return { titled, named: (isTitled ? titled : single.id) ?? head?.id ?? "" };
}

function standing(
  head:
    | { isExecutive: boolean; isOffice: boolean; isHome: boolean }
    | undefined,
) {
  if (head == null) {
    return 4;
  }
  if (head.isExecutive) {
    return 0;
  }
  if (head.isOffice) {
    return 2;
  }
  return head.isHome ? 3 : 1;
}

export function inside(polity: string, area: SQL, day: SQL) {
  return sql`
    exists (
        with recursive up(id, depth) as (
          select ap.parent_id, 1 from area_parent ap where ap.area_id = ${area} and ap.valid @> ${day}
          union
          select ap.parent_id, up.depth + 1 from up join area_parent ap on ap.area_id = up.id and ap.valid @> ${day}
          where up.depth < 8
        )
        select from polity_territory t join up on up.id = t.area_id
        where t.polity_id = ${polity}::uuid and t.valid @> ${day}
      )
  `;
}

async function races(
  election: string,
  polity: string,
  date: string | null,
  isBrief = false,
  isInside = false,
) {
  const day = date ?? "infinity";
  const [found, territory] = await Promise.all([
    db.execute<{
      id: string;
      kind: string;
      series: string;
      owner: string | null;
      office: string | null;
      elects: string | null;
      body: string | null;
      election: string;
      day: string | null;
      round: number | null;
      isOffice: boolean;
      isStage: boolean;
      isExecutive: boolean;
      isHome: boolean;
      size: number | null;
      isCounted: boolean;
      isVoting: boolean;
      isRunoff: boolean;
      previous: string | null;
      party: string | null;
      runoffs: Array<string>;
      isRemainder: boolean;
      isOwn: boolean;
      area: string | null;
      k: number | null;
      scheme: string | null;
      noun: string | null;
      grouping: "alliance" | "party";
    }>(sql`
      with recursive tree(id, stage) as (
        select ${election}::uuid, false
        union all
        select k.id, tree.stage or k.kind = 'indirect' from election k join tree on k.parent_id = tree.id
      ),
      anc(id) as (
        select ${polity}::uuid
        union
        select p.parent_id from polity_parent p join anc on p.polity_id = anc.id
      ),
      base as materialized (
        select c.id, tree.stage, coalesce(
          (select b.polity_id from body b where b.id = c.body_id),
          (select o.polity_id from office o where o.id = c.office_id),
          e.polity_id
        ) in (select id from anc) as own
        from contest c
        join election e on e.id = c.election_id
        join tree on tree.id = c.election_id
        where not c.notional and (c.allocation_parent_id is null or not exists (
          select from contest p join tree pt on pt.id = p.election_id where p.id = c.allocation_parent_id
        )) and (not ${isInside} or (c.district_area_id is null and c.kind <> 'body_seats') or ${inside(polity, sql`c.district_area_id`, sql`e.date_from`)})
      ),
      kept as materialized (
        select id, stage, own from base where own or not exists (select from base where own)
      ),
      runs as (
        select n.previous_round_id as id, array_agg(n.id) as ids
        from contest n join election ne on ne.id = n.election_id
        where ne.kind = 'runoff' and n.previous_round_id in (select id from kept)
        group by n.previous_round_id
      )
      select c.id, c.kind::text as kind, k.own as "isOwn",
        case when c.kind = 'ballot_question' then c.id::text
          else concat_ws(':', c.office_id, c.body_id, c.kind, case when e.kind <> 'general' then e.date_from end, case when e.kind = 'by_election' then c.id end, c.party_scope_id, k.stage) end as series,
        coalesce(c.body_id, c.office_id) as owner, c.office_id as office, c.election_id as election,
        (select p.office_id from contest p where p.id = c.allocation_parent_id and c.kind = 'elector_slate') as elects,
        case when c.kind = 'office' then null else c.body_id end as body,
        e.date_from::text as day, c.round_number as round,
        c.kind = 'office' as "isOffice", k.stage as "isStage", z.seats as size,
        c.kind = 'office' and exists (
          select from office o where o.id = c.office_id
            and o.kind in ('governor', 'head_of_state', 'head_of_government', 'mayor')
        ) as "isExecutive",
        exists (select from body b where b.id = c.body_id and b.polity_id = ${polity}::uuid) as "isHome",
        (c.term is distinct from 'remainder' or (c.seat_id is not null and not exists (
          select from contest d where d.election_id = c.election_id and d.seat_id = c.seat_id and d.id <> c.id
        ))) and coalesce(st.voting, true) as "isCounted",
        coalesce(st.voting, true) as "isVoting",
        e.kind = 'runoff' as "isRunoff", c.previous_round_id as previous, c.party_scope_id as party,
        coalesce(runs.ids, '{}') as runoffs,
        c.term is not distinct from 'remainder' as "isRemainder",
        a.id as area, a.k, a.scheme_code as scheme, sc.noun,
        case when exists (
          select from contest_option o where o.contest_id = c.id and o.party_id is null and o.alliance_id is not null
        ) then 'alliance' else 'party' end as grouping
      from kept k
      join contest c on c.id = k.id
      join election e on e.id = c.election_id
      left join seat st on st.id = c.seat_id
      left join area a on a.id = c.district_area_id
      left join scheme sc on sc.code = a.scheme_code
      left join lateral (
        select z.seats from body_size z
        where z.body_id = c.body_id and (z.valid @> e.date_from or z.valid_from between e.date_from and e.date_from + 150)
        order by z.valid_from desc nulls last limit 1
      ) z on true
      left join runs on runs.id = c.id
    `),
    db.execute<{ id: string; k: number }>(sql`
      select a.id, a.k from polity_territory pt join area a on a.id = pt.area_id
      where pt.polity_id = ${polity}::uuid and pt.valid @> ${day}::date limit 1
    `),
  ]);
  const nation = territory.rows[0];
  const owned = found.rows.some((row) => row.isOwn)
    ? found.rows.filter((row) => row.isOwn)
    : found.rows;
  const split = Map.groupBy(owned, (row) => row.series)
    .values()
    .flatMap((group) =>
      group.length > 1 &&
      group[0]?.area != null &&
      new Set(group.map((row) => row.area)).size === 1
        ? group.map((row) => [row])
        : [group],
    )
    .toArray();
  const chains = await decide(owned.map((row) => row.id));
  const lone = new Map(
    split.flatMap((group) =>
      group.length === 1 && group[0] != null ? [[group[0].id, group[0]]] : [],
    ),
  );
  const groups = split.filter((group) => {
    const final = chains.get(group[0]?.id ?? "")?.final;
    return (
      group.length > 1 ||
      final == null ||
      final === group[0]?.id ||
      !lone.has(final)
    );
  });
  const homes = new Map(
    groups.flatMap((group, spot) => group.map((row) => [row.id, spot])),
  );
  if (nation == null || groups.length === 0) {
    return [];
  }
  const [labels, slugs, lineage] = await Promise.all([
    names(
      owned.flatMap((row) => [
        row.id,
        ...[row.owner, row.office, row.elects, row.area, row.party].filter(
          (id) => id != null,
        ),
      ]),
      date,
    ),
    paths([
      ...owned.map((row) => row.id),
      ...chains.values().map((chain) => chain.first),
    ]),
    branches(day),
  ]);
  const parentOf = new Map(lineage.map((row) => [row.from, row.to]));
  const built = await Promise.all(
    groups.map(async (group) => {
      const roots = group.map((row) => row.id);
      const head = group[0];
      const single =
        head != null && group.length === 1 ? await find(head.id) : null;
      const place = single?.place ?? nation;
      const districts = group.filter((row) => row.k != null);
      const first = Map.groupBy(districts, (row) => row.scheme)
        .values()
        .toArray()
        .toSorted((a, b) => b.length - a.length)[0]?.[0];
      const isPrimaries =
        single == null && head?.party != null && districts.length > 1;
      const grouping =
        group.find((row) => row.grouping === "alliance")?.grouping ?? "party";
      const counted = [
        ...group.flatMap((row) =>
          row.isCounted ? [row.id, ...row.runoffs] : [],
        ),
        ...found.rows
          .filter(
            (row) =>
              head?.isRunoff === false &&
              row.isRunoff &&
              row.isCounted &&
              row.owner === head.owner &&
              row.kind === head.kind &&
              row.party === head.party &&
              found.rows.every((other) => other.id !== row.previous),
          )
          .map((row) => row.id),
      ];
      const voting = group.filter((row) => row.isCounted).map((row) => row.id);
      const [totals, grid, table, spread] = await Promise.all([
        load(
          voting.length > 0 ? voting : roots,
          [place.k],
          day,
          counted.length > 0 ? counted : roots,
        ),
        single == null || isBrief
          ? null
          : places(
              single,
              place,
              true,
              slugs.get(single.id) ?? null,
              undefined,
              null,
              lineage,
              "party",
            ),
        single != null || isBrief || first == null || districts.length < 2
          ? null
          : (async () => {
              const prior = await db.execute<{
                root: string;
                id: string;
                date: string;
              }>(
                sql`
                  select distinct on (h.id) h.id::text as root, c.id, e.date_from::text as date
                  from contest h
                  join contest c on (h.seat_id is not null and c.seat_id = h.seat_id)
                    or (h.seat_id is null and c.district_area_id = h.district_area_id)
                  join election e on e.id = c.election_id
                  where h.id = any(${sql.param(districts.map((row) => row.id))}::uuid[])
                    and c.id <> all(${sql.param(roots)}::uuid[])
                    and c.kind = h.kind
                    and c.body_id is not distinct from h.body_id
                    and c.office_id is not distinct from h.office_id
                    and c.round_number is not distinct from h.round_number
                    and c.party_scope_id is not distinct from h.party_scope_id
                    and e.date_from < ${day}::date and e.status::text <> 'annulled'
                  order by h.id, e.date_from desc, c.notional desc
                `,
              );
              const years = Map.groupBy(prior.rows, (row) =>
                row.date.slice(0, 4),
              );
              const since =
                years
                  .entries()
                  .toArray()
                  .toSorted((a, b) => b[1].length - a[1].length)[0]?.[0] ??
                null;
              const latest =
                prior.rows
                  .map((row) => row.date)
                  .toSorted((a, b) => a.localeCompare(b))
                  .at(-1) ?? null;
              const [tabled, at] = await Promise.all([
                tabulate(
                  roots,
                  districts.map((row) => ({
                    k: row.k ?? 0,
                    name: labels.full.get(row.area ?? "") ?? "",
                    path: slugs.get(row.id) ?? null,
                    isContest: true,
                    isRemainder: row.isRemainder,
                    isCounted: row.isCounted,
                    owner: row.id,
                  })),
                  date,
                  prior.rows.length === 0
                    ? null
                    : {
                        ids: prior.rows.map((row) => row.id),
                        date: latest,
                        pairs: districts.flatMap((row) => {
                          const match = prior.rows.find(
                            (entry) => entry.root === row.id,
                          );
                          return match == null
                            ? []
                            : [{ k: row.k ?? 0, id: match.id }];
                        }),
                      },
                  lineage,
                  isPrimaries ? "person" : "party",
                  isPrimaries ? "option" : grouping,
                ),
                anchors(
                  districts.map((row) => row.k ?? 0),
                  day,
                ),
              ]);
              return {
                since,
                sample: first.id,
                ...tabled,
                rows: tabled.rows.map((row) => ({
                  ...row,
                  at: at.get(row.k) ?? null,
                })),
              };
            })(),
        db.execute<{ from: string; to: string }>(sisters(roots, day)),
      ]);
      const here =
        totals.get(place.k) ??
        (single == null && districts.length > 0
          ? (
              await load(
                districts.map((row) => row.id),
                districts.map((row) => row.k ?? 0),
                day,
                counted.length > 0 ? counted : roots,
                districts.map((row) => row.id),
                null,
                "area",
                new Map<number, Array<[number, number]>>(
                  districts.map((row) => [row.k ?? 0, [[place.k, 1]]]),
                ),
              )
            ).get(place.k)
          : undefined);
      const rows = reckon(here?.side, lineage, grouping, single == null);
      const folded = Map.groupBy(rows, (row) =>
        single == null && row.cell.party != null
          ? `party:${parentOf.get(row.cell.party) ?? row.cell.party}`
          : row.key,
      );
      const parties = await names(
        [
          ...folded
            .entries()
            .flatMap(([key, members]) =>
              single == null
                ? key.startsWith("party:")
                  ? [key.slice(6)]
                  : []
                : members.flatMap((row) =>
                    row.cell.person == null ? [] : [row.cell.person],
                  ),
            ),
          ...(table?.legend ?? []).flatMap((item) =>
            item.key.startsWith("party:") ? [item.key.slice(6)] : [],
          ),
        ],
        date,
      );
      const won =
        single == null && head?.isOffice === true && roots.length > 1
          ? new Map(
              (
                await db.execute<{ party: string | null; seats: number }>(sql`
                  select coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id) as party, count(distinct x.option_id)::int as seats
                  from contest c
                  join election e on e.id = c.election_id
                  join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                  join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  join contest_option o on o.id = x.option_id
                  left join entity_merge pm on pm.entity_id = o.party_id
                  left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
                    and br.kind = 'regional_branch' and br.valid @> e.date_from
                  left join entity_merge bm on bm.entity_id = br.parent_party_id
                  where c.id = any(${sql.param(roots)}::uuid[])
                  group by 1
                `)
              ).rows.map((row) => [
                row.party == null
                  ? null
                  : `party:${parentOf.get(row.party) ?? row.party}`,
                row.seats,
              ]),
            )
          : null;
      const credited =
        single == null && head?.isOffice !== true && grouping === "alliance"
          ? (
              await db.execute<{
                from: string;
                party: string;
                seats: number;
              }>(sql`
                select r.from, r.party, count(*)::int as seats
                from (
                  select distinct on (o.id, x.candidacy_id) coalesce(lm.into_id, o.alliance_id) as from,
                    coalesce(bm.into_id, br.parent_party_id, pm.into_id, n.party_id) as party
                  from contest c
                  join election e on e.id = c.election_id
                  join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                  join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won')
                  join contest_option o on o.id = x.option_id and o.alliance_id is not null
                  join candidacy cy on cy.option_id = o.id and (x.candidacy_id is null or cy.id = x.candidacy_id)
                  cross join lateral (
                    select coalesce(
                      (select min(cn.party_id::text)::uuid from candidacy_nomination cn
                        where cn.candidacy_id = cy.id and cn.kind = 'nominated' having count(distinct cn.party_id) = 1),
                      cy.member_of_party_id
                    ) as party_id
                  ) n
                  left join entity_merge lm on lm.entity_id = o.alliance_id
                  left join entity_merge pm on pm.entity_id = n.party_id
                  left join party_affiliation br on br.party_id = coalesce(pm.into_id, n.party_id)
                    and br.kind = 'regional_branch' and br.valid @> e.date_from
                  left join entity_merge bm on bm.entity_id = br.parent_party_id
                  where c.id = any(${sql.param(roots)}::uuid[]) and n.party_id is not null
                    and (o.party_id is null or (x.candidacy_id is not null and n.party_id <> o.party_id and exists (
                      select from alliance_member am where am.alliance_id = o.alliance_id and am.party_id = n.party_id)))
                  order by o.id, x.candidacy_id, cy.id
                ) r
                group by 1, 2
              `)
            ).rows
          : [];
      const creditNames =
        credited.length === 0
          ? null
          : await names(
              credited.flatMap((row) => [
                row.from,
                parentOf.get(row.party) ?? row.party,
              ]),
              date,
            );
      const results = folded
        .entries()
        .map(([key, members]) => {
          const party = key.startsWith("party:") ? key.slice(6) : null;
          const lead =
            members.find((row) => row.key === key) ??
            members.find((row) => row.cell.party === party) ??
            members[0];
          let [votes, share, seats, ballots] = [0, 0, null as number | null, 0];
          for (const row of members) {
            votes += row.cell.votes;
            share += row.cell.share;
            ballots += row.cell.ballots;
            seats =
              row.cell.seats == null ? seats : (seats ?? 0) + row.cell.seats;
          }
          return {
            key,
            label:
              parties.full.get(
                (single == null ? party : lead?.cell.person) ?? "",
              ) ??
              lead?.label ??
              "",
            short:
              (single == null ? parties.short.get(party ?? "") : null) ??
              lead?.short ??
              null,
            color:
              (table ?? grid)?.legend.find((item) => item.key === key)?.color ??
              lead?.color ??
              null,
            lean: lead?.lean ?? null,
            person:
              members.length === 1 && (single != null || party == null)
                ? (lead?.cell.person ?? null)
                : null,
            party: party ?? lead?.cell.party ?? null,
            votes,
            share,
            seats: won == null ? seats : (won.get(key) ?? null),
            outcome: lead?.cell.outcome ?? null,
            isDeclined: lead?.cell.isDeclined === true,
            isIndependent: members.every((row) => row.cell.isIndependent),
            isWriteIn: members.every((row) => row.cell.isWriteIn),
            ballots,
          };
        })
        .filter((row) => !row.key.startsWith("kind:") || row.votes > 0)
        .toArray()
        .toSorted(
          (a, b) =>
            Number(a.key.startsWith("kind:others")) -
              Number(b.key.startsWith("kind:others")) || b.votes - a.votes,
        );
      const returned =
        single != null || head?.isOffice === true
          ? null
          : await db.execute<{
              party: string | null;
              seats: number;
              multiple: number;
            }>(sql`
              with tacit as (
                select distinct o.id, coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id) as party
                from contest c
                join election e on e.id = c.election_id
                join result_set rs on rs.contest_id = c.id and ${latest("rs")} and rs.kind = 'tacit'
                join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                join contest_option o on o.id = x.option_id
                left join entity_merge pm on pm.entity_id = o.party_id
                left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
                  and br.kind = 'regional_branch' and br.valid @> e.date_from
                left join entity_merge bm on bm.entity_id = br.parent_party_id
                where c.id = any(${sql.param(roots)}::uuid[])
              )
              select party, count(*)::int as seats,
                (select count(*)::int from contest c left join electoral_system es on es.id = c.electoral_system_id where c.id = any(${sql.param(roots)}::uuid[]) and c.magnitude > 1 and c.kind <> 'list_allocation' and coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula', '') not in ('dhondt', 'hare', 'sainte_lague_schepers', 'stv', 'imperiali_quota', 'hagenbach_bischoff', 'irv') and exists (select from contest_option co where co.contest_id = c.id and co.kind = 'candidate')) as multiple
              from tacit group by party
              union all
              select null, 0, (select count(*)::int from contest c left join electoral_system es on es.id = c.electoral_system_id where c.id = any(${sql.param(roots)}::uuid[]) and c.magnitude > 1 and c.kind <> 'list_allocation' and coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula', '') not in ('dhondt', 'hare', 'sainte_lague_schepers', 'stv', 'imperiali_quota', 'hagenbach_bischoff', 'irv') and exists (select from contest_option co where co.contest_id = c.id and co.kind = 'candidate'))
              where not exists (select from tacit)
            `);
      const repeated =
        single != null || head?.isOffice === true
          ? null
          : await db.execute<{
              party: string | null;
              name: string;
              returns: number;
            }>(sql`
              select coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id) as party,
                coalesce(min(n.text), '') as name, count(distinct c.id)::int as returns
              from contest c
              join election e on e.id = c.election_id
              join result_set rs on rs.contest_id = c.id and ${latest("rs")}
              join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
              join contest_option o on o.id = x.option_id and o.kind = 'candidate'
              join candidacy cy on cy.option_id = o.id
              left join name n on n.entity_id = cy.person_id and n.is_preferred
              left join entity_merge pm on pm.entity_id = o.party_id
              left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
                and br.kind = 'regional_branch' and br.valid @> e.date_from
              left join entity_merge bm on bm.entity_id = br.parent_party_id
              where c.id = any(${sql.param(roots)}::uuid[])
              group by cy.person_id, 1
              having count(distinct c.id) > 1
              order by 2
            `);
      const unopposed = (returned?.rows ?? []).filter((row) => row.seats > 0);
      let tacit = 0;
      for (const row of unopposed) {
        tacit += row.seats;
      }
      const level = grid?.level ?? (table == null ? null : first?.scheme);
      let filled = 0;
      for (const row of results) {
        filled += row.seats ?? 0;
      }
      const size = isInside
        ? group.filter((row) => row.isCounted).length
        : (head?.size ?? null);
      const isTier =
        head?.body != null &&
        (head.round ?? 1) === 1 &&
        (head.kind === "body_seats" || head.kind === "list_allocation") &&
        groups.some(
          (other) =>
            other[0]?.body === head.body &&
            other[0].election === head.election &&
            (other[0].round ?? 1) === 1 &&
            other[0].kind !== head.kind &&
            (other[0].kind === "body_seats" ||
              other[0].kind === "list_allocation"),
        );
      const [map, previous, runoff, seated, asked, wards, field] =
        await Promise.all([
          level == null || isBrief || isPrimaries
            ? null
            : sketch(
                place,
                level,
                day,
                (table ?? grid)?.rows.map((row) => row.k) ?? [],
                grid?.districts?.map((row) => row.k),
                head?.id ?? null,
              ),
          isBrief ||
          date == null ||
          size == null ||
          head?.owner == null ||
          head.isOffice ||
          (!isTier && filled < size * 0.9)
            ? null
            : db.execute<{
                party: string | null;
                seats: number;
                doubt: number;
                notional: boolean;
                size: number | null;
                day: string | null;
              }>(sql`
                with recursive same as (
                  select case when x.kind = 'local' then 'local' else 'general' end::election_kind as kind
                  from election x where x.id = ${head.election}::uuid
                ),
                last as (
                  select max(e.date_from) as day
                  from contest c join election e on e.id = c.election_id
                  where coalesce(c.body_id, c.office_id) = ${head.owner}::uuid
                    and c.allocation_parent_id is null and c.party_scope_id is null
                    and e.kind = (select kind from same) and e.date_from < ${date}::date - 120
                ),
                nwin as materialized (
                  select o.id, o.contest_id as contest, o.party_id
                  from contest nc
                  join election ne on ne.id = nc.election_id
                  join area na on na.id = nc.district_area_id
                  join result_set rs on rs.contest_id = nc.id and rs.is_default and rs.stage = 'notional'
                  cross join lateral (
                    select o.id, o.contest_id, o.party_id from contest_option o
                    cross join lateral (
                      select sum(y.value) as value from result_rollup y
                      where y.result_set_k = rs.k and y.area_k = na.k and y.option_k = o.k
                        and y.measure = 1 and y.count_round = 0 offset 0
                    ) y
                    where o.contest_id = nc.id and y.value is not null
                    order by y.value desc limit 1
                  ) o
                  where nc.notional and coalesce(nc.body_id, nc.office_id) = ${head.owner}::uuid
                    and ne.date_from between (select day from last) - 200 and (select day from last)
                ),
                roots as (
                  select c.id, not ${isInside} or ${inside(polity, sql`c.district_area_id`, sql`e.date_from`)} as inside,
                    (select count(*) from area_parent ap where ap.area_id = c.district_area_id and ap.valid @> e.date_from) as parents
                  from contest c join election e on e.id = c.election_id
                  left join seat st on st.id = c.seat_id
                  where coalesce(c.body_id, c.office_id) = ${head.owner}::uuid
                    and c.allocation_parent_id is null and c.party_scope_id is null and e.kind = (select kind from same)
                    and c.notional = exists (
                      select from contest nc join election ne on ne.id = nc.election_id
                      where nc.notional and coalesce(nc.body_id, nc.office_id) = ${head.owner}::uuid
                        and ne.date_from between (select day from last) - 200 and (select day from last)
                        and nc.district_area_id in (select k.district_area_id from contest k where k.election_id = ${head.election}::uuid)
                    )
                    and (c.term is distinct from 'remainder' or (c.seat_id is not null and not exists (
                      select from contest d where d.election_id = c.election_id and d.seat_id = c.seat_id and d.id <> c.id
                    ))) and coalesce(st.voting, true)
                    and e.date_from between (select day from last) - 200 and (select day from last)
                    and not exists (
                      select from contest pc join election pe on pe.id = pc.election_id
                      where pc.id = c.previous_round_id and pe.kind = (select kind from same)
                    )
                ),
                tree(id) as (
                  select id from roots where inside
                  union all
                  select c.id from contest c join roots r on c.previous_round_id = r.id and r.inside
                  union all
                  select c.id from contest c join tree on c.allocation_parent_id = tree.id
                ),
                winners as materialized (
                  select o.id, o.contest_id as contest, o.party_id, max(coalesce(x.seats, 1)) as seats
                  from tree t
                  join contest_option o on o.contest_id = t.id
                  join outcome x on x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
                  where not exists (select from contest k where k.allocation_parent_id = t.id)
                    and not (o.kind = 'candidate' and o.parent_option_id is not null and exists (
                      select from contest_option l
                      join outcome lx on lx.option_id = l.id and lx.kind in ('elected', 'won') and lx.candidacy_id is null
                      join result_set lr on lr.id = lx.result_set_id and ${latest("lr")}
                      where l.contest_id = o.contest_id and l.kind = 'list'
                    ) and (o.party_id is null or exists (
                      select from contest_option l where l.id = o.parent_option_id and l.party_id is not null
                    )))
                    and not (o.kind = 'list' and o.party_id is null and exists (
                      select from contest_option ch
                      join outcome cx on cx.option_id = ch.id and cx.kind in ('elected', 'won') and cx.candidacy_id is null
                      join result_set cr on cr.id = cx.result_set_id and ${latest("cr")}
                      where ch.parent_option_id = o.id and ch.kind = 'candidate' and ch.party_id is not null
                    ))
                  group by o.id, o.contest_id, o.party_id
                  union all
                  select n.id, n.contest, n.party_id, 1 from nwin n where n.contest in (select id from tree)
                ),
                sized as (
                  select z.seats from body_size z
                  where z.body_id = ${head.owner}::uuid and z.valid && daterange((select day from last), (select day from last) + 120)
                  order by lower(z.valid) desc limit 1
                ),
                counted as (
                  select w.* from winners w
                  where not exists (select from contest k where k.id = w.contest and k.kind = 'body_seats')
                    or (
                      select coalesce(sum(l.seats), 0) from winners l
                      join contest lc on lc.id = l.contest and lc.kind = 'list_allocation'
                    ) < 0.9 * coalesce((select seats from sized), 0)
                ),
                leads as (
                  select distinct on (ch.parent_option_id) ch.parent_option_id as parent, ch.party_id
                  from winners w
                  join contest_option ch on ch.contest_id = w.contest and ch.parent_option_id = w.id and ch.party_id is not null
                  join contest lc on lc.id = ch.contest_id
                  join election le on le.id = lc.election_id
                  left join polity_territory lt on lt.polity_id = le.polity_id and lt.valid @> le.date_from
                  join area la on la.id = coalesce(lc.district_area_id, lt.area_id)
                  join result_set rs on rs.contest_id = ch.contest_id and ${latest("rs")}
                  cross join lateral (
                    select max(y.value) as value from result_rollup y
                    where y.result_set_k = rs.k and y.area_k = la.k and y.option_k = ch.k
                  ) y
                  where w.party_id is null and y.value is not null
                  order by ch.parent_option_id, y.value desc
                ),
                now as materialized (
                  select distinct coalesce(m.into_id, o.party_id) as party
                  from contest c
                  join contest_option o on o.contest_id = c.id and o.party_id is not null
                  left join entity_merge m on m.entity_id = o.party_id
                  join outcome x on x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
                  where c.election_id = ${head.election}::uuid and coalesce(c.body_id, c.office_id) = ${head.owner}::uuid
                )
                select coalesce(bm.into_id, br.parent_party_id, lin.party, pm.into_id, w.party_id, ld.party_id) as party,
                  sum(w.seats)::int as seats,
                  (select count(*)::int from roots where ${isInside} and parents <> 1) as doubt,
                  exists (select from roots r join contest n on n.id = r.id where n.notional) as notional,
                  (select seats from sized) as size,
                  (select day::text from last) as day
                from counted w
                left join leads ld on ld.parent = w.id
                left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
                left join party_affiliation br on br.party_id = coalesce(pm.into_id, w.party_id, ld.party_id)
                  and br.kind = 'regional_branch' and br.valid @> (select day from last)
                left join entity_merge bm on bm.entity_id = br.parent_party_id
                left join lateral (
                  select n.party from party_relation r
                  cross join lateral (
                    select case when r.kind = 'successor_of' then r.to_party_id else r.from_party_id end as old,
                      case when r.kind = 'successor_of' then r.from_party_id else r.to_party_id end as new
                  ) d
                  left join entity_merge nm on nm.entity_id = d.new
                  cross join lateral (select coalesce(nm.into_id, d.new) as party) n
                  where r.kind in ('merged_into', 'absorbed_by', 'successor_of')
                    and d.old = coalesce(pm.into_id, w.party_id, ld.party_id)
                    and r.date > (select day from last) and r.date <= ${date}::date + 365
                    and n.party in (select party from now)
                    and coalesce(pm.into_id, w.party_id, ld.party_id) not in (select party from now)
                  limit 1
                ) lin on true
                group by 1
                union all
                select distinct coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id), 0, 0, false, null::int, null::text
                from tree t
                join contest_option o on o.contest_id = t.id and o.party_id is not null
                left join entity_merge pm on pm.entity_id = o.party_id
                left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
                  and br.kind = 'regional_branch' and br.valid @> (select day from last)
                left join entity_merge bm on bm.entity_id = br.parent_party_id
              `),
          sequel(group, isBrief, place.k, lineage),
          isBrief || filled === 0 || group.length < 2
            ? null
            : holders(
                [...new Set([...roots, ...counted])],
                day,
                lineage,
                new Set(results.map((row) => row.key)),
                null,
              ),
          poll(single?.id ?? null, place.k, head?.kind === "ballot_question"),
          isBrief ||
          results.some((row) => row.key.startsWith("party:")) ||
          group.length < 2 ||
          head?.kind !== "body_seats"
            ? null
            : (async () => {
                const found = await db.execute<{
                  id: string;
                  area: string | null;
                  seat: string | null;
                  person: string | null;
                  share: number | null;
                  isWon: boolean | null;
                  runoff: string | null;
                  date: string | null;
                  isGeneral: boolean | null;
                  winner: string | null;
                  won: number | null;
                  isDone: boolean | null;
                  field: Array<{
                    person: string;
                    party: string | null;
                    isIncumbent: boolean | null;
                  }> | null;
                }>(sql`
                select g.id, c.district_area_id as area, (select st.label from seat st where st.id = c.seat_id) as seat,
                  f.person, f.share, f.won as "isWon", g.runoff,
                  case when f.person is null and g.runoff is null then (
                    select json_agg(json_build_object('person', coalesce(fm.into_id, fc.person_id), 'party', coalesce(fo.party_id, fc.member_of_party_id), 'isIncumbent', fc.is_incumbent) order by fo.ballot_position nulls last, fo.k)
                    from contest_option fo
                    join candidacy fc on fc.option_id = fo.id and fc.role = 'head'
                    left join entity_merge fm on fm.entity_id = fc.person_id
                    where fo.contest_id = c.id and fo.kind = 'candidate'
                  ) end as field,
                  (select e.date_from::text from contest n join election e on e.id = n.election_id where n.id = g.runoff) as date,
                  (select e.kind = 'general' from contest n join election e on e.id = n.election_id where n.id = g.runoff) as "isGeneral",
                  r.person as winner, r.share as won, r.won as "isDone"
                from (
                  select x.id, (
                    select n.id from contest n join election ne on ne.id = n.election_id
                    where n.previous_round_id = x.id order by ne.date_from limit 1
                  ) as runoff
                  from unnest(${sql.param(group.map((row) => row.id))}::uuid[]) as x(id)
                ) g
                join contest c on c.id = g.id
                left join lateral (
                select coalesce(m.into_id, cy.person_id) as person, t.value / nullif(t.total, 0) as share,
                  exists (
                    select from outcome x join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
                    where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  ) as won
                from contest c
                join area a on a.id = c.district_area_id
                join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                cross join lateral (
                  select y.option_k, sum(y.value)::float8 as value, sum(sum(y.value)) over ()::float8 as total
                  from result_rollup y
                  where y.result_set_k = rs.k and y.area_k = a.k and y.measure = 1 and y.option_k <> 0
                    and y.count_round = (
                      select max(z.count_round) from result_rollup z
                      where z.result_set_k = rs.k and z.area_k = a.k and z.measure = 1 offset 0
                    )
                    group by y.option_k
                  offset 0
                ) t
                join contest_option o on o.k = t.option_k
                left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                left join entity_merge m on m.entity_id = cy.person_id
                where c.id = g.id
                order by t.value desc limit 1
                ) f on true
                left join lateral (
                select coalesce(m.into_id, cy.person_id) as person, t.value / nullif(t.total, 0) as share,
                  exists (
                    select from outcome x join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
                    where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  ) as won
                from contest c
                join area a on a.id = c.district_area_id
                join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                cross join lateral (
                  select y.option_k, sum(y.value)::float8 as value, sum(sum(y.value)) over ()::float8 as total
                  from result_rollup y
                  where y.result_set_k = rs.k and y.area_k = a.k and y.measure = 1 and y.option_k <> 0
                    and y.count_round = (
                      select max(z.count_round) from result_rollup z
                      where z.result_set_k = rs.k and z.area_k = a.k and z.measure = 1 offset 0
                    )
                    group by y.option_k
                  offset 0
                ) t
                join contest_option o on o.k = t.option_k
                left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                left join entity_merge m on m.entity_id = cy.person_id
                where c.id = g.runoff
                order by t.value desc limit 1
                ) r on true
              `);
                const [people, links] = await Promise.all([
                  names(
                    found.rows.flatMap((row) =>
                      [
                        row.area,
                        row.person,
                        row.winner,
                        ...(row.field ?? []).flatMap((entry) => [
                          entry.person,
                          entry.party,
                        ]),
                      ].filter((id) => id != null),
                    ),
                    date,
                  ),
                  paths(
                    found.rows.flatMap((row) =>
                      [row.id, row.runoff].filter((id) => id != null),
                    ),
                  ),
                ]);
                return found.rows
                  .map((row) => ({
                    path: links.get(row.id) ?? null,
                    label:
                      row.seat ??
                      (people.full.get(row.area ?? "") ?? "").replace(
                        /^.*, /u,
                        "",
                      ),
                    person: row.person,
                    name: people.full.get(row.person ?? "") ?? null,
                    share: row.share,
                    isWon: row.isWon === true,
                    field: (row.field ?? []).map((entry) => ({
                      person: entry.person,
                      isIncumbent: entry.isIncumbent,
                      name: people.full.get(entry.person) ?? "",
                      party:
                        entry.party == null
                          ? null
                          : (people.short.get(entry.party) ??
                            people.full.get(entry.party) ??
                            null),
                    })),
                    runoff:
                      row.runoff == null
                        ? null
                        : {
                            path: links.get(row.runoff) ?? null,
                            date: row.date ?? "",
                            isGeneral: row.isGeneral === true,
                            person: row.winner,
                            name: people.full.get(row.winner ?? "") ?? null,
                            share: row.won ?? 0,
                            isWon: row.isDone === true || (row.won ?? 0) > 0.5,
                          },
                  }))
                  .toSorted((a, b) =>
                    a.label.localeCompare(b.label, undefined, {
                      numeric: true,
                    }),
                  );
              })(),
          single == null || results.length > 0
            ? []
            : (async () => {
                const found = await db.execute<{
                  person: string;
                  party: string | null;
                  isIncumbent: boolean | null;
                }>(sql`
                select coalesce(fm.into_id, fc.person_id) as person, coalesce(fo.party_id, fc.member_of_party_id) as party, fc.is_incumbent as "isIncumbent"
                from contest_option fo
                join candidacy fc on fc.option_id = fo.id and fc.role = 'head'
                left join entity_merge fm on fm.entity_id = fc.person_id
                where fo.contest_id = ${single.id}::uuid and fo.kind = 'candidate'
                order by fo.ballot_position nulls last, fo.k
              `);
                const people = await names(
                  found.rows.flatMap((row) =>
                    [row.person, row.party].filter((id) => id != null),
                  ),
                  date,
                );
                return found.rows.map((row) => ({
                  person: row.person,
                  isIncumbent: row.isIncumbent,
                  name: people.full.get(row.person) ?? "",
                  party:
                    row.party == null
                      ? null
                      : (people.short.get(row.party) ??
                        people.full.get(row.party) ??
                        null),
                }));
              })(),
        ]);
      const straddled = Math.max(
        0,
        ...(previous?.rows ?? []).map((row) => row.doubt),
      );
      const before =
        straddled > 0 ||
        (isTier &&
          (size == null || filled < size * 0.95 || filled > size * 1.02))
          ? null
          : carried(previous?.rows ?? null, filled, parentOf);
      const { titled, named } = naming(head, single, groups);
      const chain = group.length === 1 ? chains.get(head?.id ?? "") : undefined;
      const sources = new Set(
        group.map((row) => {
          const link = chains.get(row.id);
          return link == null || link.first === row.id
            ? -1
            : (homes.get(link.first) ?? -1);
        }),
      );
      return {
        map,
        before,
        first:
          chain == null || chain.first === head?.id || chain.isPrimary
            ? null
            : { path: slugs.get(chain.first) ?? null },
        pending: chain?.next ?? null,
        isFolded:
          group.length > 1 &&
          sources.size === 1 &&
          !sources.has(-1) &&
          (groups[sources.values().next().value ?? -1]?.length ?? 0) > 1,
        isNotional:
          before != null && (previous?.rows ?? []).some((row) => row.notional),
        resized:
          (before != null || (isTier && straddled === 0)) &&
          previous?.rows[0]?.size != null &&
          previous.rows[0].size !== size
            ? previous.rows[0].size
            : null,
        whole: isTier && straddled === 0 ? (previous?.rows ?? null) : null,
        filled,
        runoff,
        wards: wards ?? undefined,
        field,
        turnout: asked.turnout,
        quorum: asked.quorum,
        contest: single?.id ?? null,
        anchor: segment(labels.full.get(head?.owner ?? head?.id ?? "") ?? ""),
        name: labels.short.get(named) ?? labels.full.get(named) ?? "",
        scope:
          labels.short.get(head?.party ?? "") ??
          labels.full.get(head?.party ?? "") ??
          null,
        path: slugs.get(single?.id ?? "") ?? null,
        kind: head?.kind ?? "",
        isOffice: head?.isOffice ?? false,
        isRemainder: head?.isRemainder ?? false,
        body: head?.body ?? null,
        isStage: head?.isStage ?? false,
        round: head?.round ?? null,
        office: head?.office ?? null,
        election: head?.election ?? "",
        day: lone.get(chain?.first ?? "")?.day ?? head?.day ?? null,
        chamber:
          labels.short.get(titled ?? "") ??
          labels.full.get(titled ?? "") ??
          null,
        isNational: head?.area == null || head.area === nation.id,
        rank: standing(head),
        count: group.length,
        noun: first?.noun ?? "",
        nonvoting: group.filter((row) => !row.isVoting).length,
        sample: table?.sample ?? null,
        since:
          table?.since ??
          (before == null && !(isTier && straddled === 0)
            ? null
            : (previous?.rows[0]?.day?.slice(0, 4) ?? null)),
        size,
        straddled,
        unopposed:
          tacit === 0
            ? null
            : {
                seats: tacit,
                parties: unopposed
                  .map((row) => {
                    const key =
                      row.party == null
                        ? "independent"
                        : `party:${parentOf.get(row.party) ?? row.party}`;
                    const shown = results.find((entry) => entry.key === key);
                    return {
                      key,
                      label: shown?.short ?? shown?.label ?? "",
                      seats: row.seats,
                    };
                  })
                  .toSorted((a, b) => b.seats - a.seats),
              },
        multiple: returned?.rows[0]?.multiple ?? 0,
        credits: credited.map((row) => {
          const party = parentOf.get(row.party) ?? row.party;
          return {
            alliance: creditNames?.full.get(row.from) ?? "",
            key: `party:${party}`,
            label: creditNames?.full.get(party) ?? "",
            short: creditNames?.short.get(party) ?? null,
            seats: row.seats,
          };
        }),
        repeats: Map.groupBy(repeated?.rows ?? [], (row) =>
          row.party == null
            ? "independent"
            : `party:${parentOf.get(row.party) ?? row.party}`,
        )
          .entries()
          .map(([key, rows]) => {
            const shown = results.find((entry) => entry.key === key);
            let extra = 0;
            for (const row of rows) {
              extra += row.returns - 1;
            }
            return {
              label: shown?.label ?? "",
              seats: shown?.seats ?? 0,
              members: (shown?.seats ?? 0) - extra,
              people: rows.map((row) => ({
                name: row.name,
                returns: row.returns,
              })),
            };
          })
          .filter((entry) => entry.label !== "" && entry.seats > 0)
          .toArray(),
        spread: spread.rows,
        measure: here?.measure ?? "votes",
        isTally: here?.isTally ?? false,
        valid: here?.side.valid ?? 0,
        results: isPrimaries ? [] : results,
        isPrimaries,
        holders:
          seated == null
            ? null
            : {
                places: seated.places.map((entry) => ({
                  name: entry.name,
                  path: entry.path,
                })),
                seats: Object.fromEntries(seated.seats),
              },
        places:
          table == null
            ? grid
            : {
                levels: [
                  {
                    code: first?.scheme ?? "",
                    noun: first?.noun ?? "",
                    count: districts.length,
                  },
                ],
                level: first?.scheme ?? "",
                ...table,
                legend: table.legend.map((item) =>
                  item.key.startsWith("party:")
                    ? {
                        ...item,
                        label:
                          parties.full.get(item.key.slice(6)) ?? item.label,
                        short: parties.short.get(item.key.slice(6)) ?? null,
                      }
                    : item,
                ),
              },
      };
    }),
  );
  const stages = built.filter(
    (race) =>
      race.isStage &&
      race.isOffice &&
      built.some(
        (other) =>
          !other.isStage &&
          other.office === race.office &&
          other.measure !== "electoral_votes",
      ),
  );
  const stagePaths = await paths(stages.map((race) => race.election));
  const loose = built.flatMap((race) =>
    race.before == null &&
    race.body == null &&
    race.kind === "body_seats" &&
    race.path != null
      ? [race.path]
      : [],
  );
  const tallied =
    loose.length === 0
      ? null
      : await db.execute<{ path: string; party: string; seats: number }>(sql`
        select s.path, coalesce(bm.into_id, br.parent_party_id, pm.into_id, o.party_id) as party, sum(x.seats)::int as seats
        from slug s
        join contest c on c.id = s.entity_id
        join election e on e.id = c.election_id
        join lateral (
          select pc.id from contest pc
          join election pe on pe.id = pc.election_id
          join slug ps on ps.entity_id = pc.id and ps.is_current
          where pe.polity_id = e.polity_id and pe.kind = e.kind and pe.date_from < e.date_from
            and pc.kind = c.kind and pc.body_id is null and pc.office_id is null
            and pc.allocation_parent_id is null
            and substring(ps.path from '[^/]+$') = substring(s.path from '[^/]+$')
          order by pe.date_from desc limit 1
        ) pc on true
        join contest_option o on o.contest_id = pc.id and o.party_id is not null
        cross join lateral (
          select max(x.seats) as seats from outcome x
          join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
          where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          having count(*) > 0
        ) x
        left join entity_merge pm on pm.entity_id = o.party_id
        left join party_affiliation br on br.party_id = coalesce(pm.into_id, o.party_id)
          and br.kind = 'regional_branch' and br.valid @> e.date_from
        left join entity_merge bm on bm.entity_id = br.parent_party_id
        where s.is_current and s.path = any(${sql.param(loose)}::text[])
        group by 1, 2
      `);
  const tallies = new Map(
    Map.groupBy(tallied?.rows ?? [], (row) => row.path)
      .entries()
      .filter(([path, rows]) => {
        const current = built.find((race) => race.path === path)?.results ?? [];
        let now = 0;
        for (const row of current) {
          now += row.seats ?? 0;
        }
        let then = 0;
        for (const row of rows) {
          then += row.seats;
        }
        return then >= now * 0.9 && then <= now * 1.1;
      }),
  );
  const chambers = built
    .filter(
      (race) =>
        race.isNational &&
        race.count === 1 &&
        (race.body != null || race.isOffice),
    )
    .map((race) => race.chamber);
  const weights = new Map<string | null, number>();
  for (const race of built) {
    weights.set(
      race.scope ?? null,
      Math.max(weights.get(race.scope ?? null) ?? 0, race.valid),
    );
  }
  return built
    .filter(
      (race) =>
        !stages.includes(race) &&
        stages.every((stage) => stage.election !== race.election),
    )
    .toSorted(
      (a, b) =>
        (a.day ?? "").localeCompare(b.day ?? "") ||
        Number(a.count === 1 && !a.isNational) -
          Number(b.count === 1 && !b.isNational) ||
        Number(a.kind === "ballot_question") -
          Number(b.kind === "ballot_question") ||
        (a.kind === "ballot_question"
          ? a.name.localeCompare(b.name, undefined, { numeric: true })
          : (weights.get(b.scope ?? null) ?? 0) -
              (weights.get(a.scope ?? null) ?? 0) ||
            a.rank - b.rank ||
            Number(a.rank === 1 && b.count === 1) -
              Number(a.rank === 1 && a.count === 1) ||
            (a.body != null &&
            a.body === b.body &&
            a.count === 1 &&
            b.count === 1
              ? a.name.localeCompare(b.name, undefined, { numeric: true })
              : 0) ||
            b.valid - a.valid),
    )
    .map(
      ({
        isStage,
        office,
        election,
        day,
        spread,
        chamber,
        isNational,
        scope,
        rank: _rank,
        whole,
        filled: _filled,
        ...race
      }) => {
        let seated = 0;
        for (const other of built) {
          if (
            other.whole != null &&
            other.body === race.body &&
            other.election === election
          ) {
            seated += other.filled;
          }
        }
        const stage = stages.find((entry) => entry.office === office);
        const rows = (
          stage == null && race.measure === "electoral_votes" ? race : stage
        )?.results;
        const name =
          isNational && race.count === 1 && (race.body != null || race.isOffice)
            ? chambers.indexOf(chamber) === chambers.lastIndexOf(chamber)
              ? (chamber ?? race.name)
              : race.isRemainder
                ? m.race_special({ name: chamber ?? race.name })
                : [
                    chamber ?? "",
                    ...race.name.split(", ").slice(1),
                    ...(race.name.match(/ \(([^)]+)\)$/u)?.slice(1) ?? []),
                  ].join(", ")
            : race.name;
        return {
          ...race,
          scope: scope ?? null,
          before:
            race.before ??
            tallies
              .get(race.path ?? "")
              ?.map((row) => ({
                key: `party:${parentOf.get(row.party) ?? row.party}`,
                seats: row.seats,
              })) ??
            null,
          overall: carried(whole, seated, parentOf),
          name:
            scope == null || name.includes(scope.split(" ", 1)[0] ?? "")
              ? name
              : `${name} (${scope})`,
          partners: spread.map((row) => [row.from, row.to]),
          electors:
            rows == null
              ? null
              : {
                  rows: rows
                    .filter((row) => row.votes > 0)
                    .map((row) => {
                      const line = race.results.find(
                        (entry) =>
                          entry.person != null && entry.person === row.person,
                      );
                      return {
                        key: row.key,
                        label: row.label,
                        short: line?.short ?? row.short,
                        color: line?.color ?? row.color,
                        person: row.person,
                        party: line?.party ?? row.party,
                        lean: line?.lean ?? row.lean,
                        votes: row.votes,
                      };
                    }),
                  appointed: stage?.size ?? race.size,
                  link: {
                    path: stagePaths.get(stage?.election ?? "") ?? null,
                    date: stage?.day ?? day,
                    popular: false,
                  },
                },
        };
      },
    );
}

export const elections = {
  locate: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/locate",
        summary: "Find the election a site path names",
      }),
    )
    .input(v.object({ path: v.string() }))
    .output(v.object({ id: v.string() }))
    .handler(async ({ input }) => {
      const found = await db.execute<{ id: string }>(sql`
        select s.entity_id as id from slug s join entity e on e.id = s.entity_id
        where s.path = ${input.path} and e.type = 'election'
        order by s.is_current desc limit 1
      `);
      const row = found.rows[0];
      if (row == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return row;
    }),
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}",
        summary:
          "Get an election and its contests, optionally only the part held inside one of its nations or provinces",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.object({
        name: v.string(),
        date: v.nullable(v.string()),
        status: v.string(),
        isIndirect: v.boolean(),
        breadcrumbs: v.array(Link),
        parent: v.nullable(Link),
        notes: v.array(v.object({ kind: v.string(), body: v.string() })),
        control: v.nullable(
          v.object({
            key: v.nullable(v.string()),
            label: v.nullable(v.string()),
            color: v.nullable(v.string()),
            seats: v.number(),
            total: v.number(),
            isRolled: v.optional(v.boolean()),
          }),
        ),
        composition: v.nullable(
          v.object({
            rows: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                before: v.nullable(v.number()),
                after: v.number(),
              }),
            ),
            hasBefore: v.boolean(),
          }),
        ),
        councils: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            up: v.number(),
            size: v.nullable(v.number()),
            parties: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                seats: v.number(),
              }),
            ),
            control: v.nullable(
              v.object({
                key: v.nullable(v.string()),
                label: v.nullable(v.string()),
                color: v.nullable(v.string()),
                seats: v.number(),
                total: v.number(),
                isRolled: v.optional(v.boolean()),
              }),
            ),
          }),
        ),
        races: v.array(
          v.object({
            name: v.string(),
            anchor: v.string(),
            path: v.nullable(v.string()),
            kind: v.string(),
            turnout: v.nullable(
              v.object({ registered: v.number(), voters: v.number() }),
            ),
            quorum: v.nullable(v.number()),
            isOffice: v.boolean(),
            isRemainder: v.boolean(),
            isPrimaries: v.boolean(),
            scope: v.nullable(v.string()),
            body: v.nullable(v.string()),
            round: v.nullable(v.number()),
            count: v.number(),
            nonvoting: v.number(),
            sample: v.nullable(v.string()),
            since: v.nullable(v.string()),
            electors: v.nullable(
              v.object({
                rows: v.array(
                  v.object({
                    key: v.string(),
                    label: v.string(),
                    short: v.nullable(v.string()),
                    color: v.nullable(v.string()),
                    person: v.nullable(v.string()),
                    party: v.nullable(v.string()),
                    lean: v.nullable(v.number()),
                    votes: v.number(),
                  }),
                ),
                appointed: v.nullable(v.number()),
                link: v.object({
                  path: v.nullable(v.string()),
                  date: v.nullable(v.string()),
                  popular: v.boolean(),
                }),
              }),
            ),
            size: v.nullable(v.number()),
            isStaggered: v.optional(v.boolean()),
            unelected: v.optional(
              v.array(
                v.object({
                  category: v.picklist(["appointed", "ex_officio"]),
                  seats: v.number(),
                }),
              ),
            ),
            covered: v.optional(v.nullable(v.number())),
            straddled: v.optional(v.number()),
            unopposed: v.optional(
              v.nullable(
                v.object({
                  seats: v.number(),
                  parties: v.array(
                    v.object({
                      key: v.string(),
                      label: v.string(),
                      seats: v.number(),
                    }),
                  ),
                }),
              ),
            ),
            multiple: v.optional(v.number()),
            credits: v.optional(
              v.array(
                v.object({
                  alliance: v.string(),
                  key: v.string(),
                  label: v.string(),
                  short: v.nullable(v.string()),
                  seats: v.number(),
                }),
              ),
            ),
            repeats: v.optional(
              v.array(
                v.object({
                  label: v.string(),
                  seats: v.number(),
                  members: v.number(),
                  people: v.array(
                    v.object({ name: v.string(), returns: v.number() }),
                  ),
                }),
              ),
            ),
            partners: v.array(v.array(v.string())),
            holders: v.nullable(
              v.object({
                places: v.array(
                  v.object({ name: v.string(), path: v.nullable(v.string()) }),
                ),
                seats: v.record(
                  v.string(),
                  v.array(
                    v.object({
                      place: v.number(),
                      name: v.nullable(v.string()),
                      position: v.nullable(v.number()),
                      person: v.nullable(v.string()),
                      party: v.nullable(v.string()),
                      affiliation: v.nullable(v.string()),
                    }),
                  ),
                ),
              }),
            ),
            measure: v.string(),
            isTally: v.boolean(),
            valid: v.number(),
            results: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                lean: v.nullable(v.number()),
                person: v.nullable(v.string()),
                party: v.nullable(v.string()),
                votes: v.number(),
                share: v.number(),
                seats: v.nullable(v.number()),
                outcome: v.nullable(v.string()),
                isDeclined: v.boolean(),
                isIndependent: v.optional(v.boolean()),
                isWriteIn: v.boolean(),
                ballots: v.number(),
                photo: Photo,
                nominated: v.array(
                  v.object({ party: v.string(), label: v.string() }),
                ),
                supported: v.array(
                  v.object({ party: v.string(), label: v.string() }),
                ),
              }),
            ),
            places: Places,
            before: v.nullable(
              v.array(v.object({ key: v.string(), seats: v.number() })),
            ),
            isNotional: v.optional(v.boolean()),
            resized: v.optional(v.nullable(v.number())),
            overall: v.nullable(
              v.array(v.object({ key: v.string(), seats: v.number() })),
            ),
            first: v.nullable(v.object({ path: v.nullable(v.string()) })),
            pending: v.nullable(
              v.object({
                id: v.string(),
                date: v.string(),
                isGeneral: v.boolean(),
              }),
            ),
            isFolded: v.boolean(),
            runoff: v.nullable(
              v.object({
                path: v.nullable(v.string()),
                date: v.nullable(v.string()),
                label: v.string(),
                short: v.nullable(v.string()),
                runner: v.nullable(v.string()),
                runnerShort: v.nullable(v.string()),
                share: v.number(),
                margin: v.number(),
                isWon: v.boolean(),
              }),
            ),
            field: v.array(
              v.object({
                person: v.string(),
                isIncumbent: v.nullable(v.boolean()),
                name: v.string(),
                party: v.nullable(v.string()),
              }),
            ),
            wards: v.optional(
              v.array(
                v.object({
                  path: v.nullable(v.string()),
                  label: v.string(),
                  person: v.nullable(v.string()),
                  name: v.nullable(v.string()),
                  share: v.nullable(v.number()),
                  isWon: v.boolean(),
                  field: v.array(
                    v.object({
                      person: v.string(),
                      isIncumbent: v.nullable(v.boolean()),
                      name: v.string(),
                      party: v.nullable(v.string()),
                    }),
                  ),
                  runoff: v.nullable(
                    v.object({
                      path: v.nullable(v.string()),
                      date: v.string(),
                      isGeneral: v.boolean(),
                      person: v.nullable(v.string()),
                      name: v.nullable(v.string()),
                      share: v.number(),
                      isWon: v.boolean(),
                    }),
                  ),
                }),
              ),
            ),
            map: v.nullable(Sketch),
          }),
        ),
        turnout: v.nullable(
          v.object({
            registered: v.number(),
            voters: v.number(),
            reporting: v.number(),
            of: v.number(),
            basis: v.optional(v.nullable(v.picklist(["valid", "rejected"]))),
            earlier: v.nullable(
              v.object({
                year: v.number(),
                registered: v.number(),
                voters: v.number(),
              }),
            ),
          }),
        ),
        coverage: v.nullable(
          v.object({ held: v.number(), of: v.number(), noun: v.string() }),
        ),
        logos: LogoMap,
        coalitions: v.array(
          v.object({ name: v.string(), parties: v.array(v.string()) }),
        ),
        contests: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            children: v.number(),
          }),
        ),
        elections: v.array(
          v.object({
            name: v.string(),
            place: v.nullable(v.string()),
            noun: v.nullable(v.string()),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            isStage: v.boolean(),
            seats: v.nullable(v.number()),
            lead: v.nullable(
              v.object({ label: v.string(), seats: v.number() }),
            ),
          }),
        ),
        seated: v.nullable(
          v.object({
            total: v.number(),
            parties: v.array(
              v.object({
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                isIndependent: v.boolean(),
                seats: v.number(),
              }),
            ),
            others: v.nullable(
              v.object({ count: v.number(), seats: v.number() }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        polity: string;
        parent: string | null;
        date: string | null;
        status: string;
        isIndirect: boolean;
      }>(sql`
        select polity_id as polity, parent_id as parent, date_from::text as date, status::text as status,
          kind = 'indirect' as "isIndirect"
        from election where id = ${input.id}::uuid
      `);
      const election = found.rows[0];
      if (election == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const within = input.within ?? null;
      if (within != null) {
        const checked = await db.execute<{ ok: boolean }>(sql`
          with recursive up(id) as (
            select pp.parent_id from polity_parent pp where pp.polity_id = ${within}::uuid
            union
            select pp.parent_id from polity_parent pp join up on pp.polity_id = up.id
          )
          select exists (select from up where id = ${election.polity}::uuid)
            and exists (select from polity p where p.id = ${within}::uuid and (p.kind = 'region' or (p.kind = 'federal_unit' and exists (
              select from polity_parent pp join slug ps on ps.entity_id = pp.parent_id and ps.is_current and ps.path = 'ca'
              where pp.polity_id = p.id
            ))))
            and exists (
              select from polity_territory t
              where t.polity_id = ${within}::uuid and t.valid @> ${election.date ?? "infinity"}::date
            ) as ok
        `);
        if (checked.rows[0]?.ok !== true) {
          throw new ORPCError("NOT_FOUND");
        }
      }
      const home = within ?? election.polity;
      const [
        contestRows,
        electionRows,
        notes,
        crumbs,
        raced,
        anchor,
        allied,
        spread,
      ] = await Promise.all([
        db.execute<{ id: string; children: number }>(sql`
          select c.id, (select count(*)::int from contest x where x.allocation_parent_id = c.id) as children
          from contest c where c.election_id = ${input.id}::uuid and c.allocation_parent_id is null and not c.notional
            and ${within == null ? sql`true` : inside(within, sql`c.district_area_id`, sql`${election.date ?? "infinity"}::date`)}
        `),
        db.execute<{
          id: string;
          date: string | null;
          kind: string;
          polity: string | null;
          noun: string | null;
          parties: Array<{
            party: string | null;
            independent: boolean;
            seats: number;
          }> | null;
          seats: number | null;
        }>(sql`
          select e.id, e.date_from::text as date, e.kind::text as kind,
            nullif(e.polity_id, p.polity_id) as polity, sc.noun, s.parties, s.seats
          from election e
          join election p on p.id = e.parent_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          left join area a on a.id = pt.area_id
          left join scheme sc on sc.code = a.scheme_code
          left join lateral (
            select jsonb_agg(jsonb_build_object('party', w.party, 'independent', w.independent, 'seats', w.seats) order by w.seats desc) as parties,
              sum(w.seats)::int as seats
            from (
              select v.party, bool_or(v.independent) as independent, sum(v.seats)::int as seats from (
                select coalesce(m.into_id, o.party_id) as party, max(coalesce(x.seats, 1)) as seats,
                  exists (select from candidacy cy where cy.option_id = o.id and cy.is_independent) as independent
                from contest c
                join body b on b.id = c.body_id and b.polity_id = p.polity_id
                join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                join contest_option o on o.id = x.option_id
                left join entity_merge m on m.entity_id = o.party_id
                where c.election_id = e.id and c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
                group by o.id, coalesce(m.into_id, o.party_id)
              ) v group by v.party
            ) w
            where exists (select from polity sp where sp.id = p.polity_id and sp.kind = 'supranational')
          ) s on true
          where e.parent_id = ${input.id}::uuid and ${within}::uuid is null
            and not exists (select from entity_merge m where m.entity_id = e.id)
        `),
        db.execute<{ kind: string; body: string }>(sql`
          select kind::text, body_md as body from note where entity_id = ${input.id}::uuid
        `),
        breadcrumbs(home, input.id),
        races(input.id, home, election.date, false, within != null),
        db.execute<{
          k: number;
          now: string | null;
          before: string | null;
          year: number | null;
        }>(sql`
          select a.k,
            coalesce(
              (select c.id from contest c where c.election_id = e.id and not c.notional limit 1),
              (select c.id from election ch join contest c on c.election_id = ch.id where ch.parent_id = e.id and ch.kind = e.kind and ch.date_from = e.date_from limit 1)
            ) as now,
            prior.contest as before, prior.year
          from election e
          join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = pt.area_id
          left join lateral (
            select coalesce(
                (select c.id from contest c where c.election_id = x.id and not c.notional limit 1),
                (select c.id from election ch join contest c on c.election_id = ch.id where ch.parent_id = x.id and ch.kind = x.kind and ch.date_from = x.date_from limit 1)
              ) as contest,
              extract(year from x.date_from)::int as year
            from election x
            where x.polity_id = e.polity_id and x.kind = e.kind and x.date_from < e.date_from
              and not exists (select from entity_merge m where m.entity_id = x.id)
              and exists (select from contest t where t.election_id = e.id and t.office_id is not null)
                = exists (select from contest u where u.election_id = x.id and u.office_id is not null)
              and (
                not exists (select from contest t where t.election_id = e.id and t.office_id is not null)
                or exists (
                  select from contest t join contest u on u.office_id = t.office_id
                  where t.election_id = e.id and u.election_id = x.id
                )
              )
              and exists (
                select from result_set rs join election y on y.id = rs.election_id
                where rs.contest_id is null and ${latest("rs")} and y.date_from = x.date_from and y.kind = x.kind
              )
            order by x.date_from desc limit 1
          ) prior on true
          where e.id = ${input.id}::uuid and ${within}::uuid is null
          limit 1
        `),
        db.execute<{ id: string; parties: Array<string> }>(sql`
          with recursive tree(id) as (
            select ${input.id}::uuid
            union all
            select e.id from election e join tree on e.parent_id = tree.id
          )
          select a.alliance_id as id, array_agg(distinct x.id::text) as parties
          from (
            select distinct o.alliance_id from contest c
            join contest_option o on o.contest_id = c.id
            where c.election_id in (select id from tree) and o.alliance_id is not null
          ) a
          join alliance_member m on m.alliance_id = a.alliance_id
          left join entity_merge pm on pm.entity_id = m.party_id
          cross join lateral (values (m.party_id), (pm.into_id)) x(id)
          where x.id is not null
          group by a.alliance_id
        `),
        db.execute<{ held: number; of: number; noun: string | null }>(sql`
          with recursive tree(id) as (
            select ${input.id}::uuid
            union all
            select e.id from election e join tree on e.parent_id = tree.id
          ),
          home as (
            select area_id from polity_territory
            where polity_id = ${election.polity}::uuid and valid @> ${election.date}::date
          ),
          kids as (
            select pp.polity_id, pt.area_id, a.scheme_code from polity_parent pp
            join polity_territory pt on pt.polity_id = pp.polity_id and pt.valid @> ${election.date}::date
            join area a on a.id = pt.area_id
            where pp.parent_id = ${election.polity}::uuid and pp.valid @> ${election.date}::date
          ),
          districts as (
            select distinct c.district_area_id as area from contest c
            where c.election_id in (select id from tree) and c.allocation_parent_id is null and not c.notional
          ),
          up(area, depth) as (
            select area, 0 from districts where area is not null
            union
            select ap.parent_id, up.depth + 1 from up cross join lateral (
              select ap.parent_id from area_parent ap
              where ap.area_id = up.area and ap.valid @> ${election.date}::date offset 0
            ) ap
            where up.depth < 6
          )
          select count(distinct kids.polity_id) filter (where kids.area_id in (select area from up))::int as held,
            count(distinct kids.polity_id)::int as of, min(s.noun) as noun
          from kids left join scheme s on s.code = kids.scheme_code
          where ${within}::uuid is null and exists (select from districts)
            and not exists (select from districts where area is null or area in (select area_id from home))
            and not exists (select from election x where x.id = ${input.id}::uuid and x.kind = 'by_election')
        `),
      ]);
      const anchored = anchor.rows[0];
      const partial = spread.rows[0];
      const [turned, prior] = await Promise.all([
        turnout(anchored?.now ?? null, anchored == null ? [] : [anchored.k]),
        turnout(anchored?.before ?? null, anchored == null ? [] : [anchored.k]),
      ]);
      const valid = Math.max(0, ...raced.map((race) => race.valid));
      const totaled = anchored == null ? undefined : turned.get(anchored.k);
      const earlier = anchored == null ? undefined : prior.get(anchored.k);
      const isCovered = (
        entry: { voters: number; reporting: number; of: number } | undefined,
      ) =>
        entry != null &&
        (entry.reporting === entry.of || entry.voters >= valid * 0.9);
      const chamber = raced.find((race) => race.body != null)?.body ?? null;
      const pooled =
        isCovered(totaled) || chamber == null
          ? []
          : (
              await db.execute<{
                isNow: boolean;
                year: number;
                registered: number | null;
                voters: number | null;
                reporting: number;
                of: number;
                basis: "valid" | "rejected" | null;
              }>(sql`
                with recursive picked as (
                  select e.id, e.date_from, true as "isNow" from election e where e.id = ${input.id}::uuid
                  union all
                  select * from (
                    select p.id, p.date_from, false from election e
                    join election p on p.polity_id = e.polity_id and p.kind = e.kind and p.date_from < e.date_from
                      and p.status = 'held' and not exists (select from entity_merge m where m.entity_id = p.id)
                      and exists (
                        select from election pe join contest pc on pc.election_id = pe.id
                        where (pe.id = p.id or pe.parent_id = p.id) and pc.body_id = ${chamber}::uuid
                      )
                    where e.id = ${input.id}::uuid and e.kind <> 'by_election'
                    order by p.date_from desc limit 1
                  ) x
                ),
                tree(id, root) as (
                  select id, id from picked
                  union all
                  select e.id, tree.root from election e join tree on e.parent_id = tree.id
                ),
                candidates as (
                  select tree.root, c.id as contest, rs.k, a.k as area, c.kind = 'body_seats' as "isSeat" from contest c
                  join tree on tree.id = c.election_id
                  join election e on e.id = c.election_id
                  join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                  join area a on a.id = c.district_area_id
                  where c.body_id = ${chamber}::uuid and c.kind in ('body_seats', 'list_allocation') and c.allocation_parent_id is null and not c.notional
                    and c.previous_round_id is null
                    and ${within == null ? sql`true` : inside(within, sql`c.district_area_id`, sql`e.date_from`)}
                ),
                seats as (
                  select root, contest, k, area from candidates d
                  where d."isSeat" or not exists (select from candidates x where x.root = d.root and x."isSeat")
                ),
                tiers as (
                  select root, k as top, k, area from seats
                  union all
                  select s.root, s.k, kr.k, ka.k from seats s
                  join contest kc on kc.allocation_parent_id = s.contest and kc.body_id = ${chamber}::uuid and not kc.notional
                  join result_set kr on kr.contest_id = kc.id and ${latest("kr")}
                  join area ka on ka.id = kc.district_area_id
                ),
                counted as (
                  select t.root, t.top, t.k = t.top as "isOwn", nullif(max(x.value) filter (where x.measure = 2), 0) as registered,
                    coalesce(max(x.value) filter (where x.measure = 14), max(x.value) filter (where x.measure = 3)) as voters,
                    max(x.value) filter (where x.measure = 4) as valid,
                    max(x.value) filter (where x.measure = 5) as invalid
                  from tiers t
                  left join lateral (
                    select x.measure, x.value from result_rollup x
                    where x.result_set_k = t.k and x.area_k = t.area and x.option_k = 0
                      and x.channel = 0 and x.count_round = 0 and x.measure in (2, 3, 4, 5, 14) offset 0
                  ) x on true
                  group by t.root, t.top, t.k
                ),
                totals as (
                  select *, coalesce(voters, valid + coalesce(invalid, 0)) as cast from counted
                ),
                pooled as (
                  select t.top, coalesce(sum(t.cast) filter (where t."isOwn"), 0) < coalesce(sum(t.cast) filter (where not t."isOwn"), 0) as "isTree"
                  from totals t group by t.top
                ),
                kept as (
                  select t.root, sum(t.registered) as registered, sum(t.voters) as voters, sum(t.invalid) as invalid, sum(t.cast) as cast
                  from totals t join pooled p on p.top = t.top
                  where t."isOwn" or p."isTree"
                  group by t.root, t.top
                )
                select p."isNow", extract(year from p.date_from)::int as year,
                  sum(k.registered) filter (where k.cast <= k.registered)::float8 as registered,
                  sum(k.cast) filter (where k.cast <= k.registered)::float8 as voters,
                  count(*) filter (where k.cast <= k.registered)::int as reporting,
                  count(*)::int as of,
                  case when bool_and(k.voters is not null) filter (where k.cast <= k.registered) then null
                    when bool_or(k.voters is null and k.invalid is not null) filter (where k.cast <= k.registered) then 'rejected'
                    else 'valid' end as basis
                from picked p join kept k on k.root = p.id
                group by p."isNow", p.date_from
              `)
            ).rows.flatMap((row) =>
              row.registered == null ||
              row.voters == null ||
              row.reporting === 0
                ? []
                : [{ ...row, registered: row.registered, voters: row.voters }],
            );
      const pooledNow = pooled.find((row) => row.isNow);
      const pooledBefore = pooled.find((row) => !row.isNow);
      const ids = [
        input.id,
        home,
        ...contestRows.rows.map((row) => row.id),
        ...electionRows.rows.flatMap((row) =>
          row.polity == null ? [row.id] : [row.id, row.polity],
        ),
        ...(election.parent == null ? [] : [election.parent]),
        ...allied.rows.map((row) => row.id),
        ...raced.flatMap((race) =>
          race.contest == null
            ? []
            : [
                ...(race.body == null ? [] : [race.body]),
                ...race.results.flatMap((row) => row.party ?? []),
              ],
        ),
      ];
      const councils = raced.filter(
        (race) =>
          race.body != null &&
          race.kind === "body_seats" &&
          (race.round ?? 1) === 1,
      );
      const [labels, slugs, faces, whole, nominations] = await Promise.all([
        names(ids),
        paths(ids),
        portraits(
          raced.flatMap((race) =>
            race.results.flatMap((row) => row.person ?? []),
          ),
        ),
        db.execute<{
          sample: string;
          election: string;
          isWhole: boolean;
          isStaggered: boolean;
          covered: number | null;
        }>(sql`
          select c.id::text as sample, c.election_id::text as election, exists (
            select from external_id x where x.entity_id = c.election_id and x.scheme = 'gb.leh.type'
              and split_part(x.value, ':', 1) in ('SDA', 'UAA', 'LB', 'SC')
          ) or (
            not exists (select from external_id x where x.entity_id = c.election_id and x.scheme = 'gb.leh.type')
            and (select e.kind = 'local' from election e where e.id = c.election_id)
            and (select sum(k.magnitude) from contest k where k.election_id = c.election_id and k.body_id = c.body_id and k.allocation_parent_id is null)
              = (select z.seats from body_size z join election e on e.id = c.election_id where z.body_id = c.body_id and z.valid @> e.date_from limit 1)
          ) as "isWhole", exists (
            select from external_id x where x.entity_id = c.election_id and x.scheme = 'gb.leh.type'
              and split_part(x.value, ':', 1) in ('SDT', 'UAT', 'MB')
          ) as "isStaggered", case when coalesce(c.rules_override ->> 'formula', es.rules ->> 'formula', '') = 'stv' then (
            select sum(k.magnitude)::int from contest k where k.election_id = c.election_id and k.body_id = c.body_id and k.allocation_parent_id is null and not k.notional
          ) end as covered
          from contest c
          left join electoral_system es on es.id = c.electoral_system_id
          where c.id = any(${sql.param(councils.flatMap((race) => race.sample ?? race.contest ?? []))}::uuid[])
        `),
        db.execute<{
          contest: string;
          person: string;
          kind: string;
          party: string;
          isOwn: boolean;
        }>(sql`
          select distinct o.contest_id as contest, coalesce(m.into_id, cy.person_id) as person,
            n.kind::text as kind, coalesce(pm.into_id, n.party_id) as party, n.party_id = o.party_id as "isOwn"
          from candidacy_nomination n
          join candidacy cy on cy.id = n.candidacy_id and cy.role = 'head'
          join contest_option o on o.id = cy.option_id
          left join entity_merge m on m.entity_id = cy.person_id
          left join entity_merge pm on pm.entity_id = n.party_id
          where o.contest_id = any(${sql.param(raced.flatMap((race) => race.contest ?? []))}::uuid[])
        `),
      ]);
      const nominators = await names(
        [...new Set(nominations.rows.map((row) => row.party))],
        election.date,
      );
      const held = new Map(whole.rows.map((row) => [row.sample, row]));
      const rolled =
        within != null || !whole.rows.some((row) => row.isStaggered)
          ? []
          : (
              await db.execute<{
                origin: string;
                isNow: boolean;
                party: string | null;
                seats: number;
                size: number | null;
                cycles: number;
                isWhole: boolean;
              }>(sql`
                with t0 as (
                  select distinct e.id as target, c.body_id as body, e.date_from as day
                  from election e join contest c on c.election_id = e.id and c.body_id is not null
                  where e.id = any(${sql.param(whole.rows.filter((row) => row.isStaggered).map((row) => row.election))}::uuid[])
                ),
                p as (
                  select distinct on (t0.target) t0.target as origin, e.id as target, t0.body, e.date_from as day
                  from t0 join contest c on c.body_id = t0.body and c.allocation_parent_id is null and not c.notional
                  join election e on e.id = c.election_id and e.kind = 'local' and e.date_from < t0.day
                  order by t0.target, e.date_from desc
                ),
                t as (
                  select target as origin, target, body, day, true as "isNow" from t0
                  union all
                  select origin, target, body, day, false from p
                ),
                runs as (
                  select t.origin, t."isNow", t.body, t.day as at, e.id as election,
                    row_number() over (partition by t.origin, t."isNow" order by e.date_from desc) as n,
                    count(*) filter (where k.magnitude > 1) * 2 > count(*) as "isWhole"
                  from t
                  join contest k on k.body_id = t.body and k.kind = 'body_seats' and k.allocation_parent_id is null and not k.notional
                  join election e on e.id = k.election_id and e.kind = 'local'
                    and e.date_from <= t.day and e.date_from > t.day - interval '6 years'
                  group by t.origin, t."isNow", t.body, t.day, e.id, e.date_from
                ),
                wnd as (
                  select r.* from runs r
                  where r.n <= coalesce((select min(x.n) from runs x where x.origin = r.origin and x."isNow" = r."isNow" and x."isWhole"), 3)
                ),
                won as (
                  select w.origin, w."isNow", w.n, w."isWhole", k.district_area_id as area, coalesce(k.magnitude, 1) as magnitude,
                    coalesce(pm.into_id, o.party_id)::text as party, y.votes
                  from wnd w
                  join contest k on k.election_id = w.election and k.body_id = w.body and k.kind = 'body_seats'
                    and k.allocation_parent_id is null and not k.notional
                  join area a on a.id = k.district_area_id
                  join result_set rs on rs.contest_id = k.id and ${latest("rs")}
                  join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
                  join contest_option o on o.id = x.option_id
                  left join entity_merge pm on pm.entity_id = o.party_id
                  left join lateral (
                    select sum(y.value) as votes from result_rollup y
                    where y.result_set_k = rs.k and y.area_k = a.k and y.option_k = o.k and y.measure = 1 and y.count_round = 0
                    offset 0
                  ) y on true
                ),
                sized as (
                  select origin, "isNow", area,
                    case when bool_or("isWhole") then max(magnitude) filter (where "isWhole") else count(distinct n) end as size
                  from won group by origin, "isNow", area
                ),
                kept as (
                  select r.* from (
                    select won.*, row_number() over (partition by origin, "isNow", area order by n, votes desc nulls last) as at
                    from won
                  ) r join sized z using (origin, "isNow", area)
                  where r.at <= z.size
                )
                select k.origin::text as origin, k."isNow", k.party, count(*)::int as seats,
                  (select z.seats from body_size z where z.body_id = (select body from t where t.origin = k.origin and t."isNow" = k."isNow")
                    and z.valid @> (select day from t where t.origin = k.origin and t."isNow" = k."isNow") limit 1) as size,
                  (select count(*)::int from wnd w where w.origin = k.origin and w."isNow" = k."isNow") as cycles,
                  (select bool_or(w."isWhole") from wnd w where w.origin = k.origin and w."isNow" = k."isNow") as "isWhole"
                from kept k
                group by k.origin, k."isNow", k.party
              `)
            ).rows;
      const compositions = new Map<string, Map<boolean, Map<string, number>>>();
      for (const [key, rows] of Map.groupBy(
        rolled,
        (row) => `${row.origin}|${row.isNow}`,
      )) {
        const first = rows[0];
        let total = 0;
        for (const row of rows) {
          total += row.seats;
        }
        if (
          first == null ||
          first.size !== total ||
          (first.cycles < 3 && !first.isWhole)
        ) {
          continue;
        }
        const [origin = ""] = key.split("|");
        const sides = compositions.get(origin) ?? new Map();
        sides.set(
          first.isNow,
          new Map(rows.map((row) => [row.party ?? "independent", row.seats])),
        );
        compositions.set(origin, sides);
      }
      const rolledParties = await refs(
        [
          ...new Set(
            rolled.flatMap((row) => (row.party == null ? [] : [row.party])),
          ),
        ],
        election.date,
      );
      const standings = councils.map((race) => {
        const found = held.get(race.sample ?? race.contest ?? "");
        let total = 0;
        for (const row of race.results) {
          total += row.seats ?? 0;
        }
        const top = race.results.toSorted(
          (a, b) => (b.seats ?? 0) - (a.seats ?? 0),
        )[0];
        const isMajority = top != null && (top.seats ?? 0) * 2 > total;
        const sides = compositions.get(found?.election ?? "");
        const after = sides?.get(true);
        let size = 0;
        for (const seats of after?.values() ?? []) {
          size += seats;
        }
        const [lead, most = 0] =
          [...(after?.entries() ?? [])].toSorted((a, b) => b[1] - a[1])[0] ??
          [];
        const party =
          lead == null || lead === "independent"
            ? null
            : rolledParties.get(lead);
        return {
          race,
          path: slugs.get(found?.election ?? "") ?? null,
          total,
          control:
            found?.isWhole === true && within == null
              ? {
                  key: isMajority ? top.key : null,
                  label: isMajority ? top.label : null,
                  color: isMajority ? top.color : null,
                  seats: top?.seats ?? 0,
                  total,
                }
              : after != null && found?.isStaggered === true
                ? {
                    key:
                      party != null && most * 2 > size
                        ? (race.results.find((row) => row.party === lead)
                            ?.key ??
                          lead ??
                          null)
                        : null,
                    label:
                      party != null && most * 2 > size
                        ? (race.results.find((row) => row.party === lead)
                            ?.label ?? party.label)
                        : null,
                    color:
                      party != null && most * 2 > size
                        ? (party.color ?? null)
                        : null,
                    seats: most,
                    total: size,
                    isRolled: true,
                  }
                : null,
          composition:
            after == null
              ? null
              : [
                  ...new Set([
                    ...after.keys(),
                    ...(sides?.get(false)?.keys() ?? []),
                  ]),
                ]
                  .map((key) => ({
                    key,
                    label:
                      key === "independent"
                        ? m.seats_independents()
                        : (rolledParties.get(key)?.label ?? ""),
                    short:
                      key === "independent"
                        ? null
                        : (rolledParties.get(key)?.short ?? null),
                    color:
                      key === "independent"
                        ? null
                        : (rolledParties.get(key)?.color ?? null),
                    before: sides?.get(false)?.get(key) ?? null,
                    after: after.get(key) ?? 0,
                  }))
                  .toSorted(
                    (a, b) =>
                      b.after - a.after ||
                      (b.before ?? 0) - (a.before ?? 0) ||
                      a.label.localeCompare(b.label),
                  ),
          hasBefore: sides?.has(false) === true,
        };
      });
      const unelected = Map.groupBy(
        (
          await db.execute<{
            body: string;
            category: "appointed" | "ex_officio";
            seats: number;
          }>(sql`
            with recursive chain as materialized (
              select t.id, t.body_id, t.from_date, t.to_date, t.acquired_via::text as via,
                coalesce(t.succeeds_tenure_id, case when t.seat_id is not null then
                  lag(t.id) over (partition by t.seat_id order by t.from_date nulls first) end) as before
              from tenure t
              where t.body_id = any(${sql.param([...new Set(raced.flatMap((race) => race.body ?? []))])}::uuid[])
            ),
            kinds(id, category) as (
              select c.id, case when c.via in ('ex_officio', 'appointed') then c.via else 'elected' end
              from chain c
              where c.before is null or c.via not in ('appointed', 'acting', 'succession', 'list_replacement', 'co_opted', 'trustee')
              union all
              select c.id, k.category from kinds k join chain c on c.before = k.id
              where c.via in ('appointed', 'acting', 'succession', 'list_replacement', 'co_opted', 'trustee')
            )
            select c.body_id::text as body, k.category, count(*)::int as seats
            from chain c join kinds k on k.id = c.id
            where k.category in ('appointed', 'ex_officio')
              and c.from_date <= ${election.date ?? "infinity"}::date + 45
              and coalesce(c.to_date, 'infinity'::date) > ${election.date ?? "infinity"}::date + 45
            group by 1, 2 order by 2
          `)
        ).rows,
        (row) => row.body,
      );
      const isUmbrella =
        within == null &&
        raced.length >= 5 &&
        standings.length === raced.length &&
        new Set(raced.map((race) => race.body)).size === raced.length &&
        new Set(whole.rows.map((row) => row.election)).size === raced.length &&
        !whole.rows.some((row) => row.election === input.id);
      const seatedRows = electionRows.rows.flatMap((row) => row.parties ?? []);
      const seatedRefs = await refs(
        seatedRows.flatMap((row) => row.party ?? []),
      );
      const seatedTotals = Map.groupBy(
        seatedRows,
        (row) => row.party ?? (row.independent ? "independent" : ""),
      );
      const seatedParties = [...seatedTotals.entries()].map(([key, rows]) => {
        let seats = 0;
        for (const row of rows) {
          seats += row.seats;
        }
        const ref = seatedRefs.get(key);
        return {
          label: ref?.label ?? "",
          short: ref?.short ?? null,
          color: ref?.color ?? null,
          isIndependent: key === "independent",
          seats,
        };
      });
      const seatedRanked = seatedParties.toSorted((a, b) => b.seats - a.seats);
      let seatedTotal = 0;
      let seatedRest = 0;
      for (const [index, party] of seatedRanked.entries()) {
        seatedTotal += party.seats;
        if (index >= 15) {
          seatedRest += party.seats;
        }
      }
      return withLogos(
        {
          name:
            within == null
              ? (labels.full.get(input.id) ?? "")
              : m.election_within({
                  name: labels.full.get(input.id) ?? "",
                  place: labels.full.get(within) ?? "",
                }),
          date: election.date,
          status: election.status,
          isIndirect: election.isIndirect,
          breadcrumbs: crumbs,
          parent:
            within != null
              ? {
                  name: labels.full.get(input.id) ?? "",
                  path: slugs.get(input.id) ?? null,
                }
              : election.parent == null
                ? null
                : {
                    name: labels.full.get(election.parent) ?? "",
                    path: slugs.get(election.parent) ?? null,
                  },
          notes: notes.rows,
          control:
            councils.length === 1 ? (standings[0]?.control ?? null) : null,
          composition:
            councils.length === 1 && standings[0]?.composition != null
              ? {
                  rows: standings[0].composition,
                  hasBefore: standings[0].hasBefore,
                }
              : null,
          councils: isUmbrella
            ? standings.map(({ race, path, total, control }) => ({
                name:
                  race.contest == null
                    ? race.name
                    : (labels.short.get(race.body ?? "") ??
                      labels.full.get(race.body ?? "") ??
                      race.name),
                path,
                up: total,
                size: race.size,
                parties: (race.contest == null
                  ? race.results
                      .filter((row) => (row.seats ?? 0) > 0)
                      .map((row) => ({
                        key: row.key,
                        label: row.label,
                        short: row.short,
                        color: row.color,
                        seats: row.seats ?? 0,
                      }))
                  : Map.groupBy(
                      race.results.filter((row) => (row.seats ?? 0) > 0),
                      (row) =>
                        row.party == null
                          ? "independent"
                          : `party:${row.party}`,
                    )
                      .entries()
                      .map(([key, [lead, ...rest]]) => {
                        let seats = lead?.seats ?? 0;
                        for (const row of rest) {
                          seats += row.seats ?? 0;
                        }
                        return {
                          key,
                          label:
                            lead?.party == null
                              ? m.seats_independents()
                              : (labels.full.get(lead.party) ??
                                lead.short ??
                                lead.label),
                          short:
                            lead?.party == null ? null : (lead.short ?? null),
                          color: lead?.color ?? null,
                          seats,
                        };
                      })
                      .toArray()
                ).toSorted((a, b) => b.seats - a.seats),
                control,
              }))
            : [],
          turnout: isUmbrella
            ? null
            : pooledNow != null && pooledNow.reporting >= pooledNow.of * 0.9
              ? {
                  registered: pooledNow.registered,
                  voters: pooledNow.voters,
                  reporting: pooledNow.reporting,
                  of: pooledNow.of,
                  basis: pooledNow.basis,
                  earlier:
                    pooledBefore == null ||
                    pooledBefore.reporting < pooledBefore.of * 0.9
                      ? null
                      : {
                          year: pooledBefore.year,
                          registered: pooledBefore.registered,
                          voters: pooledBefore.voters,
                        },
                }
              : totaled == null || !isCovered(totaled)
                ? null
                : {
                    ...totaled,
                    earlier:
                      earlier == null ||
                      anchored?.year == null ||
                      !isCovered(earlier)
                        ? null
                        : {
                            year: anchored.year,
                            registered: earlier.registered,
                            voters: earlier.voters,
                          },
                  },
          races: (isUmbrella ? [] : raced).map((race, index) => {
            const council = held.get(race.sample ?? race.contest ?? "");
            return {
              ...race,
              isStaggered: council?.isStaggered === true,
              unelected: (unelected.get(race.body ?? "") ?? []).map((row) => ({
                category: row.category,
                seats: row.seats,
              })),
              covered:
                council == null ||
                council.isWhole ||
                council.isStaggered ||
                council.covered == null ||
                race.size == null ||
                council.covered >= race.size
                  ? null
                  : council.covered,
              anchor: raced
                .slice(0, index)
                .some((other) => other.anchor === race.anchor)
                ? `${race.anchor}-${index + 1}`
                : race.anchor,
              results: race.results.map((row) => {
                const own = nominations.rows
                  .filter(
                    (entry) =>
                      row.person != null &&
                      entry.contest === race.contest &&
                      entry.person === row.person,
                  )
                  .map((entry) => ({
                    kind: entry.kind,
                    party: entry.party,
                    isOwn: entry.isOwn,
                    label:
                      nominators.short.get(entry.party) ??
                      nominators.full.get(entry.party) ??
                      "",
                  }))
                  .filter((entry) => entry.label !== "")
                  .toSorted(
                    (a, b) =>
                      Number(b.isOwn) - Number(a.isOwn) ||
                      a.label.localeCompare(b.label),
                  );
                const nominated = own.filter(
                  (entry) => entry.kind === "nominated",
                );
                return {
                  ...row,
                  photo: faces.get(row.person ?? "") ?? null,
                  nominated:
                    nominated.length > 1 || nominated[0]?.isOwn === false
                      ? nominated.map(({ party, label }) => ({ party, label }))
                      : [],
                  supported: own
                    .filter(
                      (entry) =>
                        entry.kind === "supported" ||
                        (entry.kind === "endorsed" &&
                          !entry.isOwn &&
                          nominated.every(
                            (other) => other.party !== entry.party,
                          )),
                    )
                    .map(({ party, label }) => ({ party, label })),
                };
              }),
            };
          }),
          coverage:
            isUmbrella ||
            partial?.noun == null ||
            partial.held === 0 ||
            partial.held > partial.of * 0.75
              ? null
              : { held: partial.held, of: partial.of, noun: partial.noun },
          coalitions: allied.rows.map((row) => ({
            name: labels.full.get(row.id) ?? "",
            parties: row.parties,
          })),
          contests: contestRows.rows
            .map((row) => ({
              name: labels.full.get(row.id) ?? "",
              path: slugs.get(row.id) ?? null,
              children: row.children,
            }))
            .toSorted((a, b) =>
              a.name.localeCompare(b.name, undefined, { numeric: true }),
            ),
          elections: electionRows.rows
            .map((row) => ({
              name: labels.full.get(row.id) ?? "",
              place: labels.full.get(row.polity ?? "") ?? null,
              noun: row.polity == null ? null : row.noun,
              path: slugs.get(row.id) ?? null,
              date: row.date,
              isStage: row.kind === "runoff" || row.kind === "indirect",
              seats: row.seats,
              lead:
                row.parties?.[0] == null
                  ? null
                  : {
                      label:
                        row.parties[0].party == null
                          ? ""
                          : (seatedRefs.get(row.parties[0].party)?.short ??
                            seatedRefs.get(row.parties[0].party)?.label ??
                            ""),
                      seats: row.parties[0].seats,
                    },
            }))
            .toSorted((a, b) =>
              a.name.localeCompare(b.name, undefined, { numeric: true }),
            ),
          seated:
            seatedTotal === 0
              ? null
              : {
                  total: seatedTotal,
                  parties: seatedRanked.slice(0, 15),
                  others:
                    seatedRanked.length > 15
                      ? { count: seatedRanked.length - 15, seats: seatedRest }
                      : null,
                },
        },
        election.date,
      );
    }),
  cycle: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/cycle",
        summary:
          "Trace an election's cycle: the primaries and caucuses held for its general election, in the polity and in each of its states or regions, and that general election",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        general: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            isCurrent: v.boolean(),
          }),
        ),
        primaries: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            until: v.nullable(v.string()),
            party: v.nullable(PartyRef),
            isCurrent: v.boolean(),
            winners: v.array(Winner),
          }),
        ),
        parts: v.array(
          v.object({
            place: v.string(),
            races: v.array(
              v.object({
                path: v.nullable(v.string()),
                date: v.nullable(v.string()),
                party: v.nullable(PartyRef),
                isCurrent: v.boolean(),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        polity: string | null;
        date: string | null;
        until: string | null;
        party: string | null;
        contest: string | null;
        isGeneral: boolean;
        isOwn: boolean;
      }>(sql`
        with x as materialized (
          select id, polity_id, kind, date_from as d from election where id = ${input.id}::uuid
        ),
        g as materialized (
          select x.id, x.polity_id, x.d from x where x.kind = 'general'
          union all
          (select e.id, e.polity_id, e.date_from from x
            join election e on e.polity_id = x.polity_id and e.kind = 'general' and e.date_from between x.d and x.d + 400
              and not exists (select from entity_merge m where m.entity_id = e.id)
            where x.kind in ('primary', 'caucus')
            order by e.date_from limit 1)
        ),
        prev as (
          select coalesce(max(e.date_from), (select d from g limit 1) - 400) as d
          from g join election e on e.polity_id = g.polity_id and e.kind = 'general'
            and e.date_from < g.d and e.date_from >= g.d - 400 and not exists (select from entity_merge m where m.entity_id = e.id)
        ),
        picked as materialized (
          select e.id, e.polity_id, e.date_from, e.polity_id = g.polity_id as own
          from g cross join prev
          join election e on e.kind in ('primary', 'caucus') and e.date_from > prev.d and e.date_from <= g.d
          left join election p on p.id = e.parent_id
          where (e.polity_id = g.polity_id and (p.id is null or p.polity_id <> e.polity_id))
            or (e.polity_id in (
              select pp.polity_id from polity_parent pp join polity y on y.id = pp.polity_id
              where pp.parent_id = g.polity_id and y.kind in ('federal_unit', 'region')
            ) and (p.id is null or p.polity_id <> e.polity_id))
        )
        select g.id, null as polity, g.d::text as date, null as until, null as party, null as contest,
          true as "isGeneral", true as "isOwn"
        from g
        union all
        select p.id, p.polity_id, p.date_from::text, u.until::text, s.party, s.contest, false, p.own
        from picked p
        left join lateral (
          with recursive grp(id, election_id) as (
            select c.id, c.election_id from contest c where c.election_id = p.id and c.allocation_parent_id is null
            union all
            select c.id, c.election_id from contest c join grp on c.allocation_parent_id = grp.id
          )
          select nullif(max(e.date_from), p.date_from) as until
          from grp join election e on e.id = grp.election_id
        ) u on true
        left join lateral (
          select min(c.party_scope_id::text)::uuid as party,
            case when count(*) = 1 then min(c.id::text)::uuid end as contest
          from contest c where c.election_id = p.id and c.allocation_parent_id is null
          having count(distinct c.party_scope_id) = 1 and bool_and(c.party_scope_id is not null)
        ) s on true
      `);
      const general = found.rows.find((row) => row.isGeneral) ?? null;
      const rows = found.rows.filter((row) => !row.isGeneral);
      const own = rows.filter((row) => row.isOwn);
      const won = await db.execute<{
        contest: string;
        person: string | null;
        party: string | null;
        label: string | null;
      }>(sql`
        select x.contest, cy.person_id as person, o.party_id as party, coalesce(cy.ballot_name, o.ballot_label) as label
        from unnest(${sql.param(own.flatMap((row) => row.contest ?? []))}::uuid[]) as x(contest)
        cross join lateral (
          select rs.id from result_set rs where rs.contest_id = x.contest
          order by rs.is_default desc, array_position(${STAGES}, rs.stage)
          limit 1
        ) rs
        join outcome oc on oc.result_set_id = rs.id and oc.candidacy_id is null and oc.kind in ('won', 'elected')
          and not exists (select from outcome d where d.result_set_id = rs.id and d.option_id = oc.option_id and d.kind = 'declined')
        join contest_option o on o.id = oc.option_id
        left join lateral (
          select c.person_id, c.ballot_name from candidacy c
          where c.option_id = o.id and c.role = 'head' order by c.id limit 1
        ) cy on true
        order by x.contest, o.ballot_position nulls last, o.k
      `);
      const [labels, slugs, colors] = await Promise.all([
        names(
          [
            ...found.rows.map((row) => row.id),
            ...rows.flatMap((row) => row.polity ?? []),
            ...won.rows.flatMap((row) => row.person ?? []),
          ],
          general?.date ?? null,
        ),
        paths(found.rows.map((row) => row.id)),
        refs(
          [
            ...rows.flatMap((row) => row.party ?? []),
            ...won.rows.flatMap((row) => row.party ?? []),
          ],
          general?.date ?? null,
        ),
      ]);
      const party = (id: string | null) =>
        id == null ? null : (colors.get(id) ?? null);
      return {
        general:
          general == null
            ? null
            : {
                name: labels.full.get(general.id) ?? "",
                path: slugs.get(general.id) ?? null,
                date: general.date,
                isCurrent: general.id === input.id,
              },
        primaries: own
          .map((row) => ({
            id: row.id,
            name: labels.full.get(row.id) ?? "",
            path: slugs.get(row.id) ?? null,
            date: row.date,
            until: row.until,
            party: party(row.party),
            isCurrent: row.id === input.id,
            winners: won.rows
              .filter((entry) => entry.contest === row.contest)
              .map((entry) => ({
                label:
                  labels.full.get(entry.person ?? "") ??
                  entry.label ??
                  party(entry.party)?.label ??
                  "",
                person: entry.person,
                party: entry.party,
                color: party(entry.party)?.color ?? null,
                share: null,
              })),
          }))
          .toSorted(
            (a, b) =>
              (a.date ?? "").localeCompare(b.date ?? "") ||
              a.name.localeCompare(b.name),
          ),
        parts: Map.groupBy(
          rows.filter((row) => !row.isOwn),
          (row) => row.polity ?? "",
        )
          .entries()
          .map(([polity, group]) => ({
            place: labels.full.get(polity) ?? "",
            races: group
              .map((row) => ({
                path: slugs.get(row.id) ?? null,
                date: row.date,
                party: party(row.party),
                isCurrent: row.id === input.id,
              }))
              .toSorted((a, b) => (a.date ?? "").localeCompare(b.date ?? "")),
          }))
          .toArray()
          .toSorted((a, b) => a.place.localeCompare(b.place)),
      };
    }),
  lead: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/lead",
        summary:
          "Get an election's first race with its top six by seats won, else by share, with sister parties counted together",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          name: v.string(),
          question: v.nullable(v.string()),
          quorum: v.nullable(v.number()),
          contested: v.number(),
          results: v.array(
            v.object({
              key: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
              person: v.nullable(v.string()),
              share: v.number(),
              seats: v.nullable(v.number()),
              isLost: v.optional(v.boolean()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{ polity: string; date: string | null }>(
        sql`select polity_id as polity, date_from::text as date from election where id = ${input.id}::uuid`,
      );
      const election = found.rows[0];
      if (election == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const [race] = await races(
        input.id,
        election.polity,
        election.date,
        true,
      );
      if (race == null) {
        return null;
      }
      const ruled = await db.execute<{
        isQuestion: boolean;
        quorum: number | null;
      }>(sql`
        select c.kind = 'ballot_question' as "isQuestion",
          ((coalesce(es.rules, '{}') || coalesce(c.rules_override, '{}')) ->> 'quorum')::float8 as quorum
        from contest c left join electoral_system es on es.id = c.electoral_system_id
        where c.id = ${race.contest}::uuid
      `);
      const rules = ruled.rows[0];
      let contested = 0;
      for (const row of race.results) {
        contested += row.seats ?? 0;
      }
      const joined = Map.groupBy(
        race.results,
        (row) =>
          race.partners.find((pair) => pair.includes(row.key))?.[0] ?? row.key,
      )
        .values()
        .map((rows) => {
          const [head] = rows.toSorted(
            (a, b) => (b.seats ?? 0) - (a.seats ?? 0) || b.share - a.share,
          );
          if (head == null || rows.length === 1) {
            return head;
          }
          let seats = 0;
          let share = 0;
          for (const row of rows) {
            seats += row.seats ?? 0;
            share += row.share;
          }
          return {
            ...head,
            label: rows.map((row) => row.short ?? row.label).join("/"),
            short: null,
            seats,
            share,
          };
        })
        .filter((row) => row != null)
        .toArray();
      return {
        name: race.name,
        question: rules?.isQuestion === true ? race.name : null,
        quorum: rules?.quorum ?? null,
        contested,
        results: (contested > 1
          ? joined.toSorted((a, b) => (b.seats ?? 0) - (a.seats ?? 0))
          : joined
        ).slice(0, 6),
      };
    }),
  governments: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/governments",
        summary:
          "List the government in office on an election's day and every government formed before the next general election, with their heads and parties",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.array(
        v.object({
          name: v.string(),
          head: v.nullable(v.object({ id: v.string(), name: v.string() })),
          from: v.string(),
          to: v.nullable(v.string()),
          isCaretaker: v.boolean(),
          isBefore: v.boolean(),
          size: v.nullable(v.number()),
          parties: v.array(
            v.object({
              ...PartyRef.entries,
              role: v.string(),
              seats: v.nullable(v.number()),
              lean: v.nullable(v.number()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        name: string | null;
        head: string | null;
        from: string;
        to: string | null;
        isCaretaker: boolean;
        isBefore: boolean;
        size: number | null;
      }>(sql`
        with here as (
          select e.id, e.polity_id, e.date_from,
            (select min(n.date_from) from election n
              where n.polity_id = e.polity_id and n.kind = 'general' and n.status = 'held'
                and n.parent_id is null and n.date_from > e.date_from and not exists (select from entity_merge m where m.entity_id = n.id)
                and exists (
                  select from contest c join body b on b.id = c.body_id
                  where c.election_id = n.id and b.polity_id = n.polity_id and b.kind in ('lower_house', 'unicameral')
                )) as next
          from election e where e.id = ${input.id}::uuid and e.kind = 'general'
            and exists (
              select from contest c join body b on b.id = c.body_id
              where c.election_id = e.id and b.polity_id = e.polity_id and b.kind in ('lower_house', 'unicameral')
            )
        )
        select g.id, g.head_person_id as head, g.from_date::text as from, g.to_date::text as to,
          g.caretaker as "isCaretaker",
          g.from_date <= here.date_from and g.election_id is distinct from here.id as "isBefore",
          (select bs.seats from body_size bs join body b on b.id = bs.body_id
            where b.polity_id = g.polity_id and b.kind in ('lower_house', 'unicameral') and bs.valid @> g.from_date
            order by b.kind limit 1) as size,
          (select n.text from name n where n.entity_id = g.id
            order by n.kind = 'short' desc, n.is_preferred desc, n.recorded_from desc, n.id limit 1) as name
        from here join government g on g.polity_id = here.polity_id
        where g.election_id = here.id
          or (g.from_date > here.date_from and g.from_date < coalesce(here.next, here.date_from + 2200))
          or (g.from_date <= here.date_from and coalesce(g.to_date, 'infinity') >= here.date_from
            and g.election_id is distinct from here.id)
        order by g.from_date
      `);
      const ids = found.rows.map((row) => row.id);
      const members = await db.execute<{
        government: string;
        party: string;
        role: string;
        seats: number | null;
      }>(sql`
        select gp.government_id as government, coalesce(pm.into_id, gp.party_id) as party,
          gp.role::text as role, gp.seats
        from government_party gp left join entity_merge pm on pm.entity_id = gp.party_id
        where gp.government_id = any(${sql.param(ids)}::uuid[])
      `);
      const parties = members.rows.map((row) => row.party);
      const [labels, colors, positions] = await Promise.all([
        names(found.rows.flatMap((row) => row.head ?? [])),
        refs(parties),
        db.execute<{ party_id: string; lean: number }>(sql`
          select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
          where party_id = any(${sql.param(parties)}::uuid[])
          order by party_id, valid_from desc nulls last
        `),
      ]);
      const lean = new Map(
        positions.rows.map((row) => [row.party_id, row.lean]),
      );
      const ROLES = ["head", "member", "support"];
      return found.rows.map((row) => ({
        name: row.name ?? "",
        head:
          row.head == null
            ? null
            : { id: row.head, name: labels.full.get(row.head) ?? "" },
        from: row.from,
        to: row.to,
        isCaretaker: row.isCaretaker,
        isBefore: row.isBefore,
        size: row.size,
        parties: members.rows
          .filter((member) => member.government === row.id)
          .map((member) => ({
            ...(colors.get(member.party) ?? {
              id: member.party,
              label: "",
              short: null,
              color: null,
            }),
            role: member.role,
            seats: member.seats,
            lean: lean.get(member.party) ?? null,
          }))
          .toSorted(
            (a, b) =>
              ROLES.indexOf(a.role) - ROLES.indexOf(b.role) ||
              (b.seats ?? 0) - (a.seats ?? 0),
          ),
      }));
    }),
  winners: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/winners",
        summary:
          "List who won the office in each of an election's child elections, grouped by region",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        noun: v.nullable(v.string()),
        groups: v.array(
          v.object({
            id: v.string(),
            k: v.nullable(v.number()),
            name: v.string(),
          }),
        ),
        maps: v.array(v.object({ places: v.nonNullable(Places), map: Sketch })),
        rows: v.array(
          v.object({
            path: v.string(),
            at: v.nullable(v.number()),
            group: v.nullable(v.string()),
            person: v.nullable(v.string()),
            party: v.nullable(v.string()),
            label: v.nullable(v.string()),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
            isLed: v.boolean(),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const offices = await db.execute<{ id: string }>(sql`
        select c.id from election k join contest c on c.election_id = k.id and c.kind = 'office'
        where k.parent_id = ${input.id}::uuid
      `);
      const decided = (await decide(offices.rows.map((row) => row.id)))
        .entries()
        .filter(([id, chain]) => chain.final !== id)
        .toArray();
      const found = await db.execute<{
        path: string;
        group: string | null;
        area: string | null;
        date: string;
        person: string | null;
        party: string | null;
        label: string | null;
        color: string | null;
        shaped: string | null;
        led: boolean | null;
      }>(sql`
        with top as (
          select pt.area_id as id from election e
          join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          where e.id = ${input.id}::uuid limit 1
        )
        select s.path, k.date_from::text as date, w.person, w.party, w.label, w.color, w.area, w.led, g.id as group, z.id as shaped
        from election k
        join slug s on s.entity_id = k.id and s.is_current
        left join lateral (
          select coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label, ar.id as area, pick.led,
            (select coalesce(m.into_id, cy.person_id) from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
             where cy.option_id = o.id and cy.role = 'head' order by cy.id limit 1) as person,
            ${paint(sql`coalesce(pm.into_id, o.party_id)`, sql`select ${input.id}::uuid`, sql`k.date_from`)} as color
          from contest c
          left join unnest(
            ${sql.param(decided.map(([id]) => id))}::uuid[],
            ${sql.param(decided.map(([, chain]) => chain.final))}::uuid[]
          ) as d(id, final) on d.id = c.id
          join result_set rs on rs.contest_id = coalesce(d.final, c.id) and ${latest("rs")}
          cross join lateral (
            select coalesce(c.district_area_id, (
              select pt.area_id from polity_territory pt where pt.polity_id = k.polity_id
              order by pt.valid @> k.date_from desc limit 1
            )) as id
          ) ar
          left join area da on da.id = ar.id
          cross join lateral (
            (
              select x.option_id as id, false as led from outcome x
              where x.result_set_id = rs.id and x.kind in ('elected', 'won') and x.candidacy_id is null
              order by x.option_id limit 1
            )
            union all
            (
              select o.id, true from (
                select y.option_k, sum(y.value) as value from result_rollup y
                where y.result_set_k = rs.k and y.area_k = da.k and y.measure = 1 and y.option_k <> 0
                  and y.count_round = (
                    select max(z.count_round) from result_rollup z
                    where z.result_set_k = rs.k and z.area_k = da.k and z.measure = 1 offset 0
                  )
                group by y.option_k
                offset 0
              ) t
              join contest_option o on o.k = t.option_k
              order by t.value desc limit 1
            )
            limit 1
          ) pick
          join contest_option o on o.id = pick.id
          left join entity_merge pm on pm.entity_id = o.party_id
          where c.election_id = k.id and c.kind = 'office'
          order by c.id limit 1
        ) w on true
        left join lateral (
          with recursive up(id, depth) as (
            select w.area, 0
            union all
            select p.parent_id, up.depth + 1 from area_parent p join up on p.area_id = up.id
            where up.depth < 6 and up.id <> (select id from top)
          )
          select up.id from up
          where exists (select from area_parent p where p.area_id = up.id and p.parent_id = (select id from top))
          order by up.depth limit 1
        ) g on true
        left join lateral (
          with recursive up(id, depth) as (
            select w.area, 0
            union all
            select p.parent_id, up.depth + 1 from area_parent p join up on p.area_id = up.id
            where up.depth < 6 and p.valid @> k.date_from
          )
          select up.id from up
          where exists (select from area_geometry ag where ag.area_id = up.id)
          order by up.depth limit 1
        ) z on true
        where k.parent_id = ${input.id}::uuid
      `);
      if (found.rows.length < 40) {
        return { noun: null, groups: [], maps: [], rows: [] };
      }
      const segments = new Map(
        found.rows.flatMap((row) =>
          row.group == null ? [] : [[row.path.split("/")[1], row.group]],
        ),
      );
      for (const row of found.rows) {
        row.group ??= segments.get(row.path.split("/")[1]) ?? null;
      }
      const groups = [...new Set(found.rows.flatMap((row) => row.group ?? []))];
      const date = found.rows[0]?.date ?? "infinity";
      const [labels, nouns, areas, territory] = await Promise.all([
        names(
          [
            ...groups,
            ...found.rows.flatMap((row) =>
              [row.person, row.party, row.area].filter((id) => id != null),
            ),
          ],
          found.rows[0]?.date ?? null,
        ),
        db.execute<{ noun: string }>(sql`
          select sc.noun from area a join scheme sc on sc.code = a.scheme_code
          where a.id = any(${sql.param(groups)}::uuid[])
          group by sc.noun order by count(*) desc limit 1
        `),
        db.execute<{
          id: string;
          k: number;
          scheme: string;
          noun: string;
          x: number | null;
          y: number | null;
        }>(sql`
          select a.id, a.k, a.scheme_code as scheme, sc.noun,
            ST_X(at.p) as x, ST_Y(at.p) as y
          from area a join scheme sc on sc.code = a.scheme_code
          left join lateral (
            select g.label_point, g.geom from area_geometry g
            where g.area_id = a.id and g.valid @> ${date}::date limit 1
          ) g on true
          cross join lateral (select ${spot("coalesce(g.label_point, ST_PointOnSurface(g.geom))", "a.scheme_code")} as p) at
          where a.id = any(${sql.param([...new Set([...groups, ...found.rows.flatMap((row) => [row.area, row.shaped].filter((id) => id != null))])])}::uuid[])
        `),
        db.execute<{ id: string; k: number }>(sql`
          select a.id, a.k from election e
          join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = pt.area_id
          where e.id = ${input.id}::uuid limit 1
        `),
      ]);
      const area = new Map(areas.rows.map((row) => [row.id, row]));
      const top = area.get(groups[0] ?? "")?.scheme;
      const levels = [
        groups.flatMap((group) => {
          const own = found.rows.filter((row) => row.group === group);
          const pick =
            own.find((row) => row.area === group) ??
            own.find((row) => depth(row.path) === 3);
          return pick == null ? [] : [{ row: pick, at: group }];
        }),
        Map.groupBy(
          found.rows.filter(
            (row) =>
              row.area != null &&
              area.get(row.area)?.scheme !== top &&
              area.get(row.area)?.x != null,
          ),
          (row) => row.area ?? "",
        )
          .entries()
          .flatMap(([at, rows]) => {
            const row = rows.toSorted(
              (a, b) => depth(a.path) - depth(b.path),
            )[0];
            return row == null ? [] : [{ row, at }];
          })
          .toArray(),
      ].filter((level) => level.length > 1);
      const legend = Map.groupBy(
        levels.flat().map((entry) => entry.row),
        key,
      )
        .entries()
        .toArray()
        .toSorted((a, b) => b[1].length - a[1].length)
        .map(([entry, rows]) => ({
          key: entry,
          label:
            labels.full.get(rows[0]?.party ?? rows[0]?.person ?? "") ??
            rows[0]?.label ??
            "",
          short: labels.short.get(rows[0]?.party ?? "") ?? null,
          color: rows[0]?.color ?? null,
          person: rows[0]?.party == null ? (rows[0]?.person ?? null) : null,
          party: rows[0]?.party ?? null,
        }));
      const position = new Map(legend.map((item, index) => [item.key, index]));
      const nation = territory.rows[0];
      const maps =
        nation == null
          ? []
          : await Promise.all(
              levels.map(async (level) => {
                const first = area.get(level[0]?.at ?? "");
                const map =
                  first == null
                    ? null
                    : await sketch(nation, first.scheme, date);
                return map == null || first == null
                  ? []
                  : [
                      {
                        map,
                        places: {
                          levels: [
                            {
                              code: first.scheme,
                              noun: first.noun,
                              count: level.length,
                            },
                          ],
                          level: first.scheme,
                          legend,
                          sources: [],
                          rows: level.flatMap(({ row, at }) => {
                            const place = area.get(at);
                            return place == null
                              ? []
                              : [
                                  {
                                    k: place.k,
                                    at:
                                      place.x == null || place.y == null
                                        ? null
                                        : ([place.x, place.y] as [
                                            number,
                                            number,
                                          ]),
                                    name: labels.full.get(at) ?? "",
                                    path: row.path,
                                    contest: true,
                                    isRemainder: false,
                                    valid: 0,
                                    shares: legend.map(() => 0),
                                    leader: position.get(key(row)) ?? null,
                                    margin: null,
                                    earlier: null,
                                    prior: null,
                                  },
                                ];
                          }),
                        },
                      },
                    ];
              }),
            );
      return {
        noun: nouns.rows[0]?.noun ?? null,
        maps: maps.flat(),

        groups: groups
          .map((id) => ({
            id,
            k: area.get(id)?.k ?? null,
            name: labels.full.get(id) ?? "",
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name, "tr")),
        rows: found.rows.map((row) => ({
          path: row.path,
          at: area.get(row.shaped ?? "")?.k ?? null,
          group: row.group,
          person: row.person,
          party: row.party,
          label:
            labels.full.get(row.person ?? "") ??
            labels.full.get(row.party ?? "") ??
            row.label,
          short:
            labels.short.get(row.party ?? "") ??
            labels.full.get(row.party ?? "") ??
            null,
          color: row.color,
          isLed: row.led === true,
        })),
      };
    }),
  local: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/local",
        summary:
          "List a county's local elections on one day: its own offices, then cities, school districts, and special districts, each contest with its leader and runoff",
      }),
    )
    .input(
      v.object({
        polity: v.pipe(v.string(), v.uuid()),
        date: v.pipe(v.string(), v.isoDate()),
      }),
    )
    .output(
      v.nullable(
        v.object({
          name: v.string(),
          path: v.nullable(v.string()),
          breadcrumbs: v.array(Link),
          own: v.nullable(v.string()),
          tiers: v.array(
            v.object({
              kind: v.picklist(["county", "municipal", "school", "special"]),
              governments: v.array(
                v.object({
                  name: v.string(),
                  path: v.nullable(v.string()),
                  contests: v.array(
                    v.object({
                      name: v.string(),
                      path: v.nullable(v.string()),
                      person: v.nullable(v.string()),
                      leader: v.nullable(v.string()),
                      share: v.nullable(v.number()),
                      isWon: v.boolean(),
                      runoff: v.nullable(
                        v.object({
                          path: v.nullable(v.string()),
                          date: v.string(),
                          isGeneral: v.boolean(),
                          person: v.nullable(v.string()),
                          leader: v.nullable(v.string()),
                          share: v.number(),
                          isWon: v.boolean(),
                        }),
                      ),
                    }),
                  ),
                }),
              ),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const polity = input.polity.toLowerCase();
      const found = await db.execute<{
        id: string;
        election: string;
        polity: string;
        kind: string;
        population: number | null;
        person: string | null;
        answer: string | null;
        share: number | null;
        isWon: boolean | null;
        runoff: string | null;
        date: string | null;
        isGeneral: boolean | null;
        winner: string | null;
        reply: string | null;
        won: number | null;
        isDone: boolean | null;
      }>(sql`
        with home as materialized (
          select g.county from coverage_government g
          where g.polity_id = ${polity}::uuid and g.kind = 'county' limit 1
        ),
        govs as materialized (
          select distinct on (e.id) e.id as election, e.polity_id, p.kind::text as kind, g.population,
            (select pt.area_id from polity_territory pt where pt.polity_id = e.polity_id and pt.valid @> e.date_from limit 1) as area
          from home
          join coverage_government g on g.county = home.county
          join election e on e.polity_id = g.polity_id and e.date_from = ${input.date}::date and e.status::text <> 'annulled'
          join polity p on p.id = e.polity_id
          order by e.id, g.population desc nulls last
        ),
        rows as materialized (
          select k.id, g.election, g.polity_id, g.kind, g.population, g.area, (
            select n.id from contest n join election ne on ne.id = n.election_id
            where n.previous_round_id = k.id order by ne.date_from limit 1
          ) as runoff
          from govs g
          join contest k on k.election_id = g.election and k.allocation_parent_id is null and not k.notional
        )
        select g.id, g.election, g.polity_id as polity, g.kind, g.population,
          f.person, f.answer, f.share, f.won as "isWon", g.runoff,
          (select e.date_from::text from contest n join election e on e.id = n.election_id where n.id = g.runoff) as date,
          (select e.kind = 'general' from contest n join election e on e.id = n.election_id where n.id = g.runoff) as "isGeneral",
          r.person as winner, r.answer as reply, r.share as won, r.won as "isDone"
        from rows g
        left join lateral (
          select coalesce(m.into_id, cy.person_id) as person, o.ballot_label as answer, t.value / nullif(t.total, 0) as share,
            exists (
              select from outcome x join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
              where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
            ) as won
          from contest kc
          join area a on a.id = coalesce(kc.district_area_id, g.area)
          join result_set rs on rs.contest_id = kc.id and ${latest("rs")}
          cross join lateral (
            select y.option_k, sum(y.value)::float8 as value, sum(sum(y.value)) over ()::float8 as total
            from result_rollup y
            where y.result_set_k = rs.k and y.area_k = a.k and y.measure = 1 and y.option_k <> 0
              and y.count_round = (
                select max(z.count_round) from result_rollup z
                where z.result_set_k = rs.k and z.area_k = a.k and z.measure = 1 offset 0
              )
            group by y.option_k
            offset 0
          ) t
          join contest_option o on o.k = t.option_k
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge m on m.entity_id = cy.person_id
          where kc.id = g.id
          order by t.value desc limit 1
        ) f on true
        left join lateral (
          select coalesce(m.into_id, cy.person_id) as person, o.ballot_label as answer, t.value / nullif(t.total, 0) as share,
            exists (
              select from outcome x join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
              where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
            ) as won
          from contest kc
          join area a on a.id = coalesce(kc.district_area_id, g.area)
          join result_set rs on rs.contest_id = kc.id and ${latest("rs")}
          cross join lateral (
            select y.option_k, sum(y.value)::float8 as value, sum(sum(y.value)) over ()::float8 as total
            from result_rollup y
            where y.result_set_k = rs.k and y.area_k = a.k and y.measure = 1 and y.option_k <> 0
              and y.count_round = (
                select max(z.count_round) from result_rollup z
                where z.result_set_k = rs.k and z.area_k = a.k and z.measure = 1 offset 0
              )
            group by y.option_k
            offset 0
          ) t
          join contest_option o on o.k = t.option_k
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge m on m.entity_id = cy.person_id
          where kc.id = g.runoff
          order by t.value desc limit 1
        ) r on true
      `);
      if (found.rows.length === 0) {
        return null;
      }
      const [labels, links, crumbs, own] = await Promise.all([
        names(
          [
            polity,
            ...found.rows.flatMap((row) =>
              [row.id, row.polity, row.person, row.winner].filter(
                (id) => id != null,
              ),
            ),
          ],
          input.date,
        ),
        paths([
          polity,
          ...found.rows.flatMap((row) =>
            [row.id, row.election, row.runoff].filter((id) => id != null),
          ),
        ]),
        breadcrumbs(polity),
        db.execute<{ path: string }>(sql`
          select s.path from election e join slug s on s.entity_id = e.id and s.is_current
          where e.polity_id = ${polity}::uuid and e.date_from = ${input.date}::date and e.status::text <> 'annulled'
          order by e.parent_id is null desc limit 1
        `),
      ]);
      const governments = new Map<
        string,
        {
          kind: "county" | "municipal" | "school" | "special";
          population: number;
          name: string;
          path: string | null;
          contests: Array<{
            name: string;
            path: string | null;
            person: string | null;
            leader: string | null;
            share: number | null;
            isWon: boolean;
            runoff: {
              path: string | null;
              date: string;
              isGeneral: boolean;
              person: string | null;
              leader: string | null;
              share: number;
              isWon: boolean;
            } | null;
          }>;
        }
      >();
      for (const row of found.rows) {
        const government = governments.get(row.election) ?? {
          kind:
            row.polity === polity || row.kind === "county"
              ? "county"
              : row.kind === "municipality" || row.kind === "submunicipal"
                ? "municipal"
                : row.kind === "school_district"
                  ? "school"
                  : "special",
          population: row.population ?? 0,
          name: labels.full.get(row.polity) ?? "",
          path: links.get(row.election) ?? null,
          contests: [],
        };
        let name = (labels.full.get(row.id) ?? "").replace(/^\d{4} /u, "");
        while (
          government.name !== "" &&
          name.startsWith(government.name) &&
          /^[:,]? ./u.test(name.slice(government.name.length))
        ) {
          name = name.slice(government.name.length).replace(/^[:,]? /u, "");
        }
        name = name
          .replace(/^[^,]+, (?=(?:Proposition|Measure|Question) \S+$)/u, "")
          .replace(/^mayoral election$/iu, "Mayor")
          .replace(/^(.+?) election(?=, |$)/u, "$1");
        government.contests.push({
          name: name.charAt(0).toUpperCase() + name.slice(1),
          path: links.get(row.id) ?? null,
          person: row.person,
          leader:
            (row.person == null ? null : labels.full.get(row.person)) ??
            row.answer,
          share: row.share,
          isWon: row.isWon === true,
          runoff:
            row.runoff == null || row.date == null
              ? null
              : {
                  path: links.get(row.runoff) ?? null,
                  date: row.date,
                  isGeneral: row.isGeneral === true,
                  person: row.winner,
                  leader:
                    (row.winner == null ? null : labels.full.get(row.winner)) ??
                    row.reply,
                  share: row.won ?? 0,
                  isWon: row.isDone === true || (row.won ?? 0) > 0.5,
                },
        });
        governments.set(row.election, government);
      }
      const ranked = [...governments.values()].toSorted(
        (a, b) => b.population - a.population || a.name.localeCompare(b.name),
      );
      return {
        name: labels.full.get(polity) ?? "",
        path: links.get(polity) ?? null,
        breadcrumbs: crumbs,
        own: own.rows[0]?.path ?? null,
        tiers: (["county", "municipal", "school", "special"] as const)
          .map((kind) => ({
            kind,
            governments: ranked
              .filter((government) => government.kind === kind)
              .map((government) => ({
                name: government.name,
                path: government.path,
                contests: government.contests.toSorted(
                  (a, b) =>
                    Number(!a.name.startsWith("Mayor")) -
                      Number(!b.name.startsWith("Mayor")) ||
                    a.name.localeCompare(b.name, undefined, { numeric: true }),
                ),
              })),
          }))
          .filter((tier) => tier.governments.length > 0),
      };
    }),
  counties: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/counties",
        summary:
          "Count a US state's local elections on one day by county, largest county first, each with its governments",
      }),
    )
    .input(
      v.object({
        polity: v.pipe(v.string(), v.uuid()),
        date: v.pipe(v.string(), v.isoDate()),
      }),
    )
    .output(
      v.object({
        contests: v.number(),
        counties: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            governments: v.array(
              v.object({
                name: v.string(),
                path: v.nullable(v.string()),
                contests: v.number(),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        election: string;
        polity: string;
        county: string | null;
        contests: number;
      }>(sql`
        with st as materialized (
          select substr(p.iso_code, 4) as code from polity p
          where p.id = ${input.polity}::uuid and p.kind = 'federal_unit' and p.iso_code like 'US-%'
        ),
        govs as materialized (
          select distinct on (e.id) e.id as election, e.polity_id, g.county, g.kind, g.population
          from st
          join coverage_government g on g.state = st.code
          join election e on e.polity_id = g.polity_id and e.date_from = ${input.date}::date and e.status::text <> 'annulled'
          order by e.id, g.population desc nulls last
        )
        select g.election, g.polity_id as polity, c.polity_id as county,
          (select count(*)::int from contest k where k.election_id = g.election and k.allocation_parent_id is null and not k.notional) as contests
        from govs g
        left join lateral (
          select x.polity_id, x.population from coverage_government x
          where x.county = g.county and x.kind = 'county' limit 1
        ) c on true
        order by c.population desc nulls last, g.kind = 'county' desc, g.kind = 'special' asc, g.population desc nulls last
      `);
      const held = found.rows.filter((row) => row.contests > 0);
      const counties = [...new Set(held.flatMap((row) => row.county ?? []))];
      const [labels, links] = await Promise.all([
        names([...counties, ...held.map((row) => row.polity)], input.date),
        paths([...counties, ...held.map((row) => row.election)]),
      ]);
      let contests = 0;
      for (const row of held) {
        contests += row.contests;
      }
      return {
        contests,
        counties: Map.groupBy(held, (row) => row.county ?? "")
          .entries()
          .map(([county, rows]) => {
            const home = links.get(county);
            return {
              name: labels.full.get(county) ?? "",
              path: home == null ? null : `${home}/${input.date}`,
              governments: rows.map((row) => ({
                name: labels.full.get(row.polity) ?? "",
                path: links.get(row.election) ?? null,
                contests: row.contests,
              })),
            };
          })
          .toArray(),
      };
    }),
  alongside: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/alongside",
        summary:
          "List the contests other governments held the same day across a local election's territory",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.array(
        v.object({
          name: v.string(),
          path: v.nullable(v.string()),
          contests: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              leader: v.nullable(v.string()),
              share: v.nullable(v.number()),
              isWon: v.boolean(),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        election: string;
        polity: string;
        date: string;
        overlap: number;
        person: string | null;
        answer: string | null;
        share: number | null;
        isWon: boolean | null;
      }>(sql`
        with me as materialized (
          select e.id, e.date_from, e.polity_id, g.geom
          from election e
          join polity p on p.id = e.polity_id and p.kind in ('municipality', 'submunicipal', 'school_district', 'special_district')
          join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area_geometry g on g.area_id = pt.area_id and g.valid @> e.date_from
          where e.id = ${input.id}::uuid
          limit 1
        ),
        day as materialized (
          select o.id, o.polity_id, pt.area_id
          from me
          join election o on o.date_from = me.date_from and o.id <> me.id and o.polity_id <> me.polity_id and o.status::text <> 'annulled'
          join polity_territory pt on pt.polity_id = o.polity_id and pt.valid @> o.date_from
        ),
        hit as materialized (
          select d.id, d.polity_id, d.area_id, x.overlap
          from day d cross join me
          join area_geometry g on g.area_id = d.area_id and g.valid @> me.date_from and g.geom && me.geom
          cross join lateral (
            select case when st_covers(g.geom, me.geom) then 1
              else st_area(st_intersection(g.geom, me.geom)) / nullif(least(st_area(g.geom), st_area(me.geom)), 0) end as overlap
          ) x
          where x.overlap >= 0.02
        )
        select c.id, h.id as election, h.polity_id as polity, me.date_from::text as date, h.overlap,
          top.person, top.answer, top.share, top.won as "isWon"
        from hit h cross join me
        join contest c on c.election_id = h.id and c.allocation_parent_id is null and not c.notional
        left join lateral (
          select coalesce(m.into_id, cy.person_id) as person, o.ballot_label as answer, t.value / nullif(t.total, 0) as share,
            exists (
              select from outcome x join result_set xs on xs.id = x.result_set_id and ${latest("xs")}
              where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
            ) as won
          from area a
          join result_set rs on rs.contest_id = c.id and ${latest("rs")}
          cross join lateral (
            select y.option_k, sum(y.value)::float8 as value, sum(sum(y.value)) over ()::float8 as total
            from result_rollup y
            where y.result_set_k = rs.k and y.area_k = a.k and y.measure = 1 and y.option_k <> 0
              and y.count_round = (
                select max(z.count_round) from result_rollup z
                where z.result_set_k = rs.k and z.area_k = a.k and z.measure = 1 offset 0
              )
              group by y.option_k
            offset 0
          ) t
          join contest_option o on o.k = t.option_k
          left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
          left join entity_merge m on m.entity_id = cy.person_id
          where a.id = coalesce(c.district_area_id, h.area_id)
          order by t.value desc limit 1
        ) top on true
        where c.district_area_id is null
          or not exists (select from area_geometry dg where dg.area_id = c.district_area_id)
          or exists (
            select from area_geometry dg
            where dg.area_id = c.district_area_id and dg.valid @> me.date_from and dg.geom && me.geom
              and (st_covers(dg.geom, me.geom)
                or st_area(st_intersection(dg.geom, me.geom)) / nullif(least(st_area(dg.geom), st_area(me.geom)), 0) >= 0.02)
          )
      `);
      const day = found.rows[0]?.date ?? null;
      const [labels, links] = await Promise.all([
        names(
          found.rows.flatMap((row) =>
            [row.id, row.polity, row.person].filter((id) => id != null),
          ),
          day,
        ),
        paths(found.rows.flatMap((row) => [row.id, row.election])),
      ]);
      const groups = new Map<
        string,
        {
          overlap: number;
          name: string;
          path: string | null;
          contests: Array<{
            name: string;
            path: string | null;
            leader: string | null;
            share: number | null;
            isWon: boolean;
          }>;
        }
      >();
      for (const row of found.rows) {
        const group = groups.get(row.election) ?? {
          overlap: row.overlap,
          name: labels.full.get(row.polity) ?? "",
          path: links.get(row.election) ?? null,
          contests: [],
        };
        let name = (labels.full.get(row.id) ?? "").replace(/^\d{4} /u, "");
        while (
          group.name !== "" &&
          name.startsWith(group.name) &&
          /^[:,]? ./u.test(name.slice(group.name.length))
        ) {
          name = name.slice(group.name.length).replace(/^[:,]? /u, "");
        }
        group.contests.push({
          name: name.charAt(0).toUpperCase() + name.slice(1),
          path: links.get(row.id) ?? null,
          leader:
            (row.person == null ? null : labels.full.get(row.person)) ??
            row.answer,
          share: row.share,
          isWon: row.isWon === true,
        });
        groups.set(row.election, group);
      }
      return [...groups.values()]
        .toSorted(
          (a, b) => b.overlap - a.overlap || a.name.localeCompare(b.name),
        )
        .map((group) => ({
          name: group.name,
          path: group.path,
          contests: group.contests.toSorted((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true }),
          ),
        }));
    }),
  regional: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/regional",
        summary:
          "Add up a multi-district race's votes by region, against the chamber's previous general election",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        contest: v.pipe(v.string(), v.uuid()),
        level: v.optional(v.string()),
      }),
    )
    .output(
      v.nullable(
        v.object({
          places: v.nonNullable(Places),
          since: v.nullable(v.string()),
          map: v.nullable(Sketch),
          world: v.nullable(v.number()),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        date: string;
        grouping: "alliance" | "party";
      }>(sql`
        with recursive tree(id) as (
          select ${input.id}::uuid
          union all
          select k.id from election k join tree on k.parent_id = tree.id
        ),
        s as (
          select c.*, e.date_from, e.kind as election_kind from contest c join election e on e.id = c.election_id
          where c.id = ${input.contest}::uuid
        )
        select c.id, e.date_from::text as date,
          case when exists (
            select from contest_option o where o.contest_id = c.id and o.party_id is null and o.alliance_id is not null
          ) then 'alliance' else 'party' end as grouping
        from s, contest c
        join tree on tree.id = c.election_id
        join election e on e.id = c.election_id
        left join seat st on st.id = c.seat_id
        where c.kind = s.kind and c.body_id is not distinct from s.body_id
          and c.office_id is not distinct from s.office_id
          and c.party_scope_id is not distinct from s.party_scope_id
          and c.round_number is not distinct from s.round_number
          and e.date_from = s.date_from and c.district_area_id is not null and not c.notional
          and (c.allocation_parent_id is null or not exists (
            select from contest p join tree pt on pt.id = p.election_id where p.id = c.allocation_parent_id
          ))
          and (c.term is distinct from 'remainder' or (c.seat_id is not null and not exists (
            select from contest d where d.election_id = c.election_id and d.seat_id = c.seat_id and d.id <> c.id
          ))) and coalesce(st.voting, true)
      `);
      const roots = found.rows.map((row) => row.id);
      const date = found.rows[0]?.date;
      if (date == null || roots.length < 2) {
        return null;
      }
      const [levels, territory, previous, lineage] = await Promise.all([
        db.execute<{ scheme: string; noun: string; ks: Array<number> }>(sql`
          with hits as (
            select distinct r.area_k
            from result_set rs
            cross join lateral (
              select distinct x.area_k from result_rollup x where x.result_set_k = rs.k
            ) r
            where rs.contest_id = any(${sql.param(roots)}::uuid[]) and rs.status = 'active'
          )
          select a.scheme_code as scheme, sc.noun, array_agg(a.k order by a.k) as ks
          from hits h join area a on a.k = h.area_k join scheme sc on sc.code = a.scheme_code
          where a.scheme_code not in (
            select d.scheme_code from contest c join area d on d.id = c.district_area_id
            where c.id = any(${sql.param(roots)}::uuid[])
          )
          group by 1, 2 having count(*) >= 2
            and (a.scheme_code = 'world.country' or count(*) < ${roots.length})
          order by count(*)
        `),
        db.execute<{ id: string; k: number; polity: string }>(sql`
          select a.id, a.k, e.polity_id as polity
          from election e join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> ${date}::date
          join area a on a.id = pt.area_id
          where e.id = ${input.id}::uuid limit 1
        `),
        db.execute<{ id: string; date: string }>(sql`
          with s as (
            select c.*, e.kind as election_kind from contest c join election e on e.id = c.election_id
            where c.id = ${input.contest}::uuid
          ),
          candidates as (
            select c.* from s join contest c on c.body_id = s.body_id
            union all
            select c.* from s join contest c on c.office_id = s.office_id
            where s.body_id is null
          ),
          pool as (
            select c.id, e.date_from from s, candidates c
            join election e on e.id = c.election_id
            left join seat st on st.id = c.seat_id
            where c.kind = s.kind and c.body_id is not distinct from s.body_id
              and c.office_id is not distinct from s.office_id
              and c.party_scope_id is not distinct from s.party_scope_id
              and c.round_number is not distinct from s.round_number
              and e.kind = s.election_kind and c.district_area_id is not null and not c.notional
              and e.date_from < ${date}::date - 120 and e.status::text <> 'annulled'
              and (c.term is distinct from 'remainder' or (c.seat_id is not null and not exists (
                select from contest d where d.election_id = c.election_id and d.seat_id = c.seat_id and d.id <> c.id
              ))) and coalesce(st.voting, true)
              and (c.allocation_parent_id is null) = (s.allocation_parent_id is null)
          )
          select id, date_from::text as date from pool
          where date_from between (select max(date_from) from pool) - 200 and (select max(date_from) from pool)
        `),
        branches(date),
      ]);
      const level =
        levels.rows.find((row) => row.scheme === input.level) ??
        levels.rows.find((row) => row.scheme !== "world.country");
      const nation = territory.rows[0];
      if (level == null || nation == null) {
        return null;
      }
      const areas = await db.execute<{
        id: string;
        k: number;
        x: number | null;
        y: number | null;
      }>(sql`
        select a.id, a.k,
          ST_X(at.p) as x, ST_Y(at.p) as y
        from area a
        left join lateral (
          select g.label_point, g.geom from area_geometry g
          where g.area_id = a.id and g.valid @> ${date}::date limit 1
        ) g on true
        cross join lateral (select ${spot("coalesce(g.label_point, ST_PointOnSurface(g.geom))", "a.scheme_code")} as p) at
        where a.k = any(${sql.param(level.ks)}::int[])
      `);
      const [labels, slugs] = await Promise.all([
        names(
          areas.rows.map((row) => row.id),
          date,
        ),
        db.execute<{ id: string; path: string }>(sql`
          select pt.area_id as id, s.path
          from polity_territory pt
          join election e on e.polity_id = pt.polity_id
          join slug s on s.entity_id = e.id and s.is_current
          where pt.area_id = any(${sql.param(areas.rows.map((row) => row.id))}::uuid[])
            and pt.valid @> ${date}::date and e.parent_id = ${input.id}::uuid
        `),
      ]);
      const linked = new Map(slugs.rows.map((row) => [row.id, row.path]));
      const prior = previous.rows
        .map((row) => row.date)
        .toSorted((a, b) => a.localeCompare(b))
        .at(-1);
      const [table, map] = await Promise.all([
        tabulate(
          roots,
          areas.rows
            .map((row) => ({
              k: row.k,
              name: labels.full.get(row.id) ?? "",
              path: linked.get(row.id) ?? null,
              isContest: false,
              owner: null,
            }))
            .toSorted((a, b) => a.name.localeCompare(b.name)),
          date,
          previous.rows.length === 0
            ? null
            : { ids: previous.rows.map((row) => row.id), date: prior ?? null },
          lineage,
          "party",
          found.rows.find((row) => row.grouping === "alliance")?.grouping ??
            "party",
        ),
        sketch(nation, level.scheme, date, level.ks),
      ]);
      const parties = await names(
        table.legend.flatMap((item) =>
          item.key.startsWith("party:") ? [item.key.slice(6)] : [],
        ),
        date,
      );
      const at = new Map(
        areas.rows.map((row) => [
          row.k,
          row.x == null || row.y == null
            ? null
            : ([row.x, row.y] as [number, number]),
        ]),
      );
      return {
        since: prior?.slice(0, 4) ?? null,
        map,
        world:
          levels.rows.find((row) => row.scheme === "world.country")?.ks
            .length ?? null,
        places: {
          levels: [
            { code: level.scheme, noun: level.noun, count: areas.rows.length },
          ],
          level: level.scheme,
          ...table,
          legend: table.legend.map((item) =>
            item.key.startsWith("party:")
              ? {
                  ...item,
                  label: parties.full.get(item.key.slice(6)) ?? item.label,
                  short: parties.short.get(item.key.slice(6)) ?? null,
                }
              : item,
          ),
          rows: table.rows.map((row) => ({
            ...row,
            at: at.get(row.k) ?? null,
          })),
        },
      };
    }),
  regions: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/regions",
        summary:
          "Count the seats each party won in each region for an election's own chambers",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.array(
        v.object({
          name: v.string(),
          noun: v.nullable(v.string()),
          regions: v.array(
            v.object({
              name: v.string(),
              scope: v.nullable(v.string()),
              seats: v.number(),
              parties: v.array(
                v.object({
                  party: v.nullable(v.string()),
                  isIndependent: v.boolean(),
                  seats: v.number(),
                }),
              ),
            }),
          ),
          parties: v.array(
            v.object({
              id: v.string(),
              label: v.string(),
              short: v.nullable(v.string()),
              color: v.nullable(v.string()),
              lean: v.nullable(v.number()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        body: string;
        kind: string;
        size: number | null;
        leaf: string;
        region: string | null;
        scheme: string | null;
        party: string | null;
        isIndependent: boolean;
        seats: number;
      }>(sql`
        with recursive tree(id) as (
          select ${input.id}::uuid
          union all
          select k.id from election k join tree on k.parent_id = tree.id where k.kind not in ('runoff', 'indirect')
        ),
        roots as materialized (
          select c.id, c.body_id as body, c.kind::text as kind, e.date_from as day
          from contest c
          join tree on tree.id = c.election_id
          join election e on e.id = c.election_id
          join body b on b.id = c.body_id and b.polity_id = (select polity_id from election where id = ${input.id}::uuid)
          left join seat st on st.id = c.seat_id
          where c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
            and c.kind in ('body_seats', 'list_allocation')
            and (c.term is distinct from 'remainder' or (c.seat_id is not null and not exists (
              select from contest d where d.election_id = c.election_id and d.seat_id = c.seat_id and d.id <> c.id
            ))) and coalesce(st.voting, true)
        ),
        grp(id, root) as (
          select id, id from roots
          union all
          select c.id, grp.root from contest c join grp on c.allocation_parent_id = grp.id
        ),
        leaves as materialized (
          select g.id, g.root, c.district_area_id as area, r.day
          from grp g join contest c on c.id = g.id join roots r on r.id = g.root
          where not exists (select from contest k where k.allocation_parent_id = g.id)
        ),
        winners as materialized (
          select l.id as leaf, l.root, o.id, o.party_id, o.alliance_id, o.kind = 'candidate' and exists (select from candidacy cd where cd.option_id = o.id and cd.is_independent) as independent, max(coalesce(x.seats, 1))::int as seats
          from leaves l
          join result_set rs on rs.contest_id = l.id and ${latest("rs")}
          join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and x.kind in ('elected', 'won')
          join contest_option o on o.id = x.option_id
          group by l.id, l.root, o.id, o.party_id, o.alliance_id, o.kind
        ),
        leads as (
          select distinct on (ch.parent_option_id) ch.parent_option_id as parent, ch.party_id
          from winners w
          join contest_option ch on ch.contest_id = w.leaf and ch.parent_option_id = w.id and ch.party_id is not null
          join contest lc on lc.id = ch.contest_id
          join election le on le.id = lc.election_id
          left join polity_territory lt on lt.polity_id = le.polity_id and lt.valid @> le.date_from
          join area la on la.id = coalesce(lc.district_area_id, lt.area_id)
          join result_set rs on rs.contest_id = ch.contest_id and ${latest("rs")}
          cross join lateral (
            select max(y.value) as value from result_rollup y
            where y.result_set_k = rs.k and y.area_k = la.k and y.option_k = ch.k
          ) y
          where w.party_id is null and y.value is not null
          order by ch.parent_option_id, y.value desc
        ),
        up(leaf, area, day, depth) as (
          select l.id, l.area, l.day, 0 from leaves l where l.area is not null
          union all
          select up.leaf, ap.parent_id, up.day, up.depth + 1
          from up join area_parent ap on ap.area_id = up.area and ap.valid @> up.day
          where up.depth < 6
        )
        select r.body, r.kind,
          (select z.seats from body_size z where z.body_id = r.body
            and (z.valid @> r.day or z.valid_from between r.day and r.day + 150)
            order by z.valid_from desc nulls last limit 1) as size,
          w.leaf, u.area as region, u.scheme as scheme,
          coalesce(bm.into_id, br.parent_party_id, pm.into_id, w.party_id, ld.party_id, w.alliance_id) as party,
          w.independent as "isIndependent", w.seats
        from winners w
        join roots r on r.id = w.root
        cross join lateral (
          select up.area, a.scheme_code as scheme from up join area a on a.id = up.area
          where up.leaf = w.leaf and up.depth > 0
          union all
          select null, null from leaves l where l.id = w.leaf and l.area is null
        ) u
        left join leads ld on ld.parent = w.id
        left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
        left join party_affiliation br on br.party_id = coalesce(pm.into_id, w.party_id, ld.party_id)
          and br.kind = 'regional_branch' and br.valid @> r.day
        left join entity_merge bm on bm.entity_id = br.parent_party_id
      `);
      const best = Map.groupBy(found.rows, (row) => row.body)
        .values()
        .flatMap((all) => {
          const kinds = Map.groupBy(all, (row) => row.kind);
          const size = all[0]?.size ?? null;
          return [...kinds.values(), ...(kinds.size > 1 ? [all] : [])]
            .flatMap((all) => {
              const rows = all.filter((row) => row.region != null);
              const unplaced = all.filter((row) => row.region == null);
              const leaves = new Set(rows.map((row) => row.leaf)).size;
              const levels = Map.groupBy(rows, (row) => row.scheme)
                .entries()
                .map(([scheme, group]) => ({
                  scheme,
                  rows: group,
                  covered: new Set(group.map((row) => row.leaf)),
                  regions: new Set(group.map((row) => row.region)).size,
                }))
                .toArray();
              const chosen = levels
                .filter(
                  (entry) =>
                    entry.covered.size === leaves && entry.regions >= 4,
                )
                .toSorted((a, b) => a.regions - b.regions)[0];
              const finer = levels
                .filter(
                  (entry) =>
                    chosen != null &&
                    entry.covered.size < leaves &&
                    entry.covered.size * 2 >= leaves &&
                    entry.regions > chosen.regions,
                )
                .toSorted((a, b) => b.covered.size - a.covered.size)[0];
              const rest = (chosen?.rows ?? []).filter(
                (row) => finer != null && !finer.covered.has(row.leaf),
              );
              const shared = new Set(
                (chosen?.rows ?? [])
                  .filter((row) => finer?.covered.has(row.leaf) === true)
                  .map((row) => row.region),
              );
              const mixed =
                finer == null ||
                rest.length === 0 ||
                rest.some((row) => shared.has(row.region))
                  ? null
                  : [...finer.rows, ...rest, ...unplaced];
              const counted = [...(chosen?.rows ?? []), ...unplaced];
              let seats = 0;
              for (const row of counted) {
                seats += row.seats;
              }
              return chosen == null ||
                seats < (size ?? seats) * 0.9 ||
                seats > (size ?? seats) * 1.02
                ? []
                : [
                    {
                      body: rows[0]?.body ?? "",
                      scheme: chosen.scheme ?? "",
                      seats,
                      rows: counted,
                      mixed:
                        mixed == null || finer == null
                          ? null
                          : { scheme: finer.scheme ?? "", rows: mixed },
                    },
                  ];
            })
            .toSorted((a, b) => b.seats - a.seats)
            .slice(0, 1)
            .flatMap(({ mixed, ...chamber }) =>
              mixed == null ? [chamber] : [chamber, { ...chamber, ...mixed }],
            );
        })
        .toArray();
      const parties = [
        ...new Set(
          best.flatMap((chamber) =>
            chamber.rows.flatMap((row) => row.party ?? []),
          ),
        ),
      ];
      const members = await db.execute<{ alliance: string; party: string }>(sql`
        select alliance_id as alliance, party_id as party from alliance_member
        where alliance_id = any(${sql.param(parties)}::uuid[])
      `);
      const held = Map.groupBy(
        best.flatMap((chamber) => chamber.rows),
        (row) => row.party,
      );
      const anchor = new Map(
        Map.groupBy(members.rows, (row) => row.alliance)
          .entries()
          .map(([alliance, rows]) => [
            alliance,
            rows.toSorted(
              (a, b) =>
                (held.get(b.party)?.length ?? 0) -
                (held.get(a.party)?.length ?? 0),
            )[0]?.party ?? alliance,
          ]),
      );
      const when = await db.execute<{ day: string }>(sql`
        select date_from::text as day from election where id = ${input.id}::uuid
      `);
      const [labels, colors, positions, nouns, scopes] = await Promise.all([
        names(
          [
            ...best.map((chamber) => chamber.body),
            ...best.flatMap((chamber) =>
              chamber.rows.map((row) => row.region ?? row.leaf),
            ),
          ],
          null,
        ),
        refs([...parties, ...anchor.values()], when.rows[0]?.day ?? null),
        db.execute<{ party_id: string; lean: number }>(sql`
          select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
          where party_id = any(${sql.param([...parties, ...anchor.values()])}::uuid[])
          order by party_id, valid_from desc nulls last
        `),
        db.execute<{ code: string; noun: string | null }>(sql`
          select code, noun from scheme where code = any(${sql.param(best.map((chamber) => chamber.scheme))}::text[])
        `),
        db.execute<{ area: string; path: string }>(sql`
          select pt.area_id as area, s.path from polity_territory pt
          join polity p on p.id = pt.polity_id
          join slug s on s.entity_id = pt.polity_id and s.is_current
          where (p.kind = 'region' or (p.kind = 'federal_unit' and exists (
              select from polity_parent pp join slug ps on ps.entity_id = pp.parent_id and ps.is_current and ps.path = 'ca'
              where pp.polity_id = p.id
            ))) and pt.area_id = any(${sql.param([...new Set(best.flatMap((chamber) => chamber.rows.flatMap((row) => row.region ?? [])))])}::uuid[])
            and pt.valid @> ${when.rows[0]?.day ?? "infinity"}::date
        `),
      ]);
      const scoped = new Map(scopes.rows.map((row) => [row.area, row.path]));
      const lean = new Map(
        positions.rows.map((row) => [row.party_id, row.lean]),
      );
      const noun = new Map(nouns.rows.map((row) => [row.code, row.noun]));
      return best.map((chamber) => {
        const titles = [
          ...new Set(chamber.rows.flatMap((row) => row.region ?? [])),
        ].map((region) => labels.full.get(region) ?? "");
        const isCollegiate = chamber.rows.every(
          (row) =>
            row.region != null ||
            /universit/iu.test(labels.full.get(row.leaf) ?? ""),
        );
        const lead = titles[0]?.split(", ", 1)[0] ?? "";
        const shared =
          titles.length > 1 &&
          titles.every((title) => title.startsWith(`${lead}, `))
            ? lead.length + 2
            : 0;
        return {
          name:
            labels.short.get(chamber.body) ??
            labels.full.get(chamber.body) ??
            "",
          noun: noun.get(chamber.scheme) ?? null,
          regions: Map.groupBy(chamber.rows, (row) => row.region)
            .entries()
            .map(([region, rows]) => {
              const tally = Map.groupBy(
                rows,
                (row) => row.party ?? (row.isIndependent ? "independent" : ""),
              )
                .entries()
                .map(([party, group]) => {
                  let won = 0;
                  for (const row of group) {
                    won += row.seats;
                  }
                  return {
                    party: group[0]?.party ?? null,
                    isIndependent: party === "independent",
                    seats: won,
                  };
                })
                .toArray()
                .toSorted((a, b) => b.seats - a.seats);
              let won = 0;
              for (const entry of tally) {
                won += entry.seats;
              }
              return {
                name:
                  region == null
                    ? isCollegiate
                      ? m.regions_universities()
                      : m.regions_unplaced()
                    : (labels.full.get(region) ?? "").slice(shared),
                scope: scoped.get(region ?? "") ?? null,
                isPlaced: region != null,
                isFiner: rows[0]?.scheme === chamber.scheme,
                seats: won,
                parties: tally,
              };
            })
            .toArray()
            .toSorted(
              (a, b) =>
                Number(b.isPlaced) - Number(a.isPlaced) ||
                Number(b.isFiner) - Number(a.isFiner) ||
                a.name.localeCompare(b.name, undefined, { numeric: true }),
            )
            .map(({ isPlaced: _, isFiner: __, ...entry }) => entry),
          parties: [
            ...new Set(chamber.rows.flatMap((row) => row.party ?? [])),
          ].map((id) => ({
            id,
            label: colors.get(id)?.label ?? "",
            short: colors.get(id)?.short ?? null,
            color: colors.get(anchor.get(id) ?? id)?.color ?? null,
            lean: lean.get(anchor.get(id) ?? id) ?? null,
          })),
        };
      });
    }),
  tiers: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/tiers",
        summary:
          "Summarize an election's parts down the ballot: the top race's winner, governors, legislative seats, and ballot measures in each part",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        parts: v.array(
          v.object({
            id: v.string(),
            code: v.string(),
            place: v.string(),
            path: v.nullable(v.string()),
            top: v.nullable(v.string()),
          }),
        ),
        tiers: v.array(
          v.object({
            kind: v.picklist(["governor", "lower", "upper", "measure"]),
            rows: v.array(
              v.object({
                part: v.string(),
                races: v.number(),
                passed: v.number(),
                tacit: v.number(),
                parties: v.array(
                  v.object({
                    party: v.nullable(v.string()),
                    isIndependent: v.boolean(),
                    isNonpartisan: v.boolean(),
                    seats: v.number(),
                  }),
                ),
              }),
            ),
          }),
        ),
        parties: v.array(
          v.object({
            id: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
            lean: v.nullable(v.number()),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        part: string;
        polity: string;
        tier: string | null;
        party: string | null;
        independent: boolean;
        nonpartisan: boolean;
        seats: number;
        races: number;
        passed: number;
        tacit: number;
      }>(sql`
        with parts as materialized (
          select e.id, e.polity_id, e.date_from from election e
          where e.parent_id = ${input.id}::uuid and e.kind not in ('runoff', 'indirect')
            and e.polity_id <> (select polity_id from election where id = ${input.id}::uuid)
            and not exists (select from entity_merge m where m.entity_id = e.id)
        ),
        roots as materialized (
          select c.id, p.id as part,
            case when c.kind = 'ballot_question' then 'measure'
              when o.kind = 'head_of_state' or c.kind = 'elector_slate' then 'top'
              when o.kind = 'governor' then 'governor'
              when b.kind = 'lower_house' then 'lower'
              else 'upper' end as tier
          from parts p
          join contest c on c.election_id = p.id and not c.notional
          left join office o on o.id = c.office_id
          left join body b on b.id = c.body_id
          where c.party_scope_id is null and (
            (c.allocation_parent_id is null and c.kind = 'ballot_question')
            or (c.allocation_parent_id is null and o.kind = 'governor' and o.polity_id = p.polity_id)
            or (c.allocation_parent_id is null and b.kind in ('lower_house', 'upper_house', 'unicameral') and b.polity_id = p.polity_id)
            or ((o.kind = 'head_of_state' or c.kind = 'elector_slate') and not exists (select from contest k where k.allocation_parent_id = c.id))
          )
        ),
        winners as materialized (
          select r.id as contest, r.part, r.tier, o.id, o.party_id, max(coalesce(x.seats, 1)) as seats, bool_and(rs.kind = 'tacit') as tacit
          from roots r
          join result_set rs on rs.contest_id = r.id and ${latest("rs")}
          join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and x.kind in ('elected', 'won')
          join contest_option o on o.id = x.option_id
          where r.tier <> 'measure'
          group by r.id, r.part, r.tier, o.id, o.party_id
        ),
        lines as materialized (
          select ch.parent_option_id as parent, ch.party_id, ch.k, ch.contest_id, a.k as area
          from winners w join contest_option ch on ch.contest_id = w.contest and ch.parent_option_id = w.id
          join contest c on c.id = ch.contest_id
          join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          where w.party_id is null and ch.party_id is not null
        ),
        leads as (
          select distinct on (l.parent) l.parent, l.party_id
          from lines l
          join result_set rs on rs.contest_id = l.contest_id and ${latest("rs")}
          cross join lateral (
            select max(y.value) as value from result_rollup y
            where y.result_set_k = rs.k and y.area_k = l.area and y.option_k = l.k
          ) y
          where y.value is not null
          order by l.parent, y.value desc
        ),
        measures as (
          select r.part, count(*)::int as races,
            count(*) filter (where exists (
              select from result_set rs
              join outcome x on x.result_set_id = rs.id and x.kind = 'won'
              join contest_option o on o.id = x.option_id
              where rs.contest_id = r.id and ${latest("rs")} and o.ballot_label ~* '^(yes|for|approve)'
            ))::int as passed
          from roots r where r.tier = 'measure'
          group by r.part
        )
        select w.part, p.polity_id as polity, w.tier, n.party,
          n.party is null and n.partisan and exists (select from candidacy cy where cy.option_id = w.id and cy.is_independent) as independent,
          n.party is null and not n.partisan as nonpartisan,
          sum(w.seats)::int as seats, count(distinct w.contest)::int as races, 0 as passed,
          coalesce(sum(w.seats) filter (where w.tacit), 0)::int as tacit
        from winners w
        join parts p on p.id = w.part
        left join leads ld on ld.parent = w.id
        left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
        left join party_affiliation br on br.party_id = coalesce(pm.into_id, w.party_id, ld.party_id)
          and br.kind = 'regional_branch' and br.valid @> p.date_from
        left join entity_merge bm on bm.entity_id = br.parent_party_id
        cross join lateral (
          select coalesce(bm.into_id, br.parent_party_id, pm.into_id, w.party_id, ld.party_id,
            (select min(cn.party_id::text)::uuid from candidacy cy
              join candidacy_nomination cn on cn.candidacy_id = cy.id and cn.kind = 'nominated'
              where cy.option_id = w.id having count(distinct cn.party_id) = 1)) as party,
            exists (select from contest_option k left join candidacy cy on cy.option_id = k.id
              where k.contest_id in (select w.contest union all select c2.id from contest c0
                join contest c2 on c2.body_id = c0.body_id and c2.election_id = c0.election_id where c0.id = w.contest)
                and (k.party_id is not null or k.alliance_id is not null or cy.member_of_party_id is not null
                  or exists (select from candidacy_nomination cn where cn.candidacy_id = cy.id))) as partisan
        ) n
        group by w.part, p.polity_id, w.tier, n.party, 5, 6
        union all
        select m.part, p.polity_id, 'measure', null, false, false, 0, m.races, m.passed, 0
        from measures m join parts p on p.id = m.part
        union all
        select p.id, p.polity_id, null, null, false, false, 0, 0, 0, 0 from parts p
      `);
      const parts = [...new Set(found.rows.map((row) => row.part))];
      const polities = [...new Set(found.rows.map((row) => row.polity))];
      const parties = [
        ...new Set(found.rows.flatMap((row) => row.party ?? [])),
      ];
      const [labels, slugs, colors, positions] = await Promise.all([
        names(polities),
        paths([...parts, ...polities]),
        refs(parties),
        db.execute<{ party_id: string; lean: number }>(sql`
          select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
          where party_id = any(${sql.param(parties)}::uuid[])
          order by party_id, valid_from desc nulls last
        `),
      ]);
      const lean = new Map(
        positions.rows.map((row) => [row.party_id, row.lean]),
      );
      const owner = new Map(found.rows.map((row) => [row.part, row.polity]));
      return {
        parts: parts
          .map((part) => {
            const polity = owner.get(part) ?? "";
            const top = found.rows
              .filter((row) => row.part === part && row.tier === "top")
              .toSorted((a, b) => b.seats - a.seats)[0];
            return {
              id: part,
              code: slugs.get(polity)?.split("/").at(-1) ?? "",
              place: labels.full.get(polity) ?? "",
              path: slugs.get(part) ?? null,
              top: top?.party ?? null,
            };
          })
          .toSorted((a, b) => a.place.localeCompare(b.place)),
        tiers: (["governor", "lower", "upper", "measure"] as const)
          .map((kind) => ({
            kind,
            rows: Map.groupBy(
              found.rows.filter((row) => row.tier === kind),
              (row) => row.part,
            )
              .entries()
              .map(([part, group]) => {
                let races = 0;
                let passed = 0;
                let tacit = 0;
                for (const row of group) {
                  races += row.races;
                  passed += row.passed;
                  tacit += row.tacit;
                }
                return {
                  part,
                  races,
                  passed,
                  tacit,
                  parties: group
                    .filter((row) => row.seats > 0)
                    .map((row) => ({
                      party: row.party,
                      isIndependent: row.independent,
                      isNonpartisan: row.nonpartisan,
                      seats: row.seats,
                    }))
                    .toSorted((a, b) => b.seats - a.seats),
                };
              })
              .toArray(),
          }))
          .filter((tier) => tier.rows.length > 0),
        parties: parties.map((id) => ({
          id,
          label: colors.get(id)?.label ?? "",
          short: colors.get(id)?.short ?? null,
          color: colors.get(id)?.color ?? null,
          lean: lean.get(id) ?? null,
        })),
      };
    }),
};

export async function record(input: { id: string; body?: string }) {
  const found = await db.execute<{
    seat: string;
    isOffice: boolean;
    isAbroad: boolean;
    bucket: string;
    link: string | null;
    date: string;
    year: number;
    races: number;
    party: string | null;
    isIndependent: boolean;
    seats: number;
    share: number | null;
    winner: string | null;
    decider: string | null;
    isRunoff: boolean;
  }>(sql`
    with recursive later as materialized (
      select distinct r.parent_id as parent, r.id as election, coalesce(rc.office_id, rc.body_id) as seat
      from election g join election r on r.parent_id = g.id join contest rc on rc.election_id = r.id
      where g.polity_id = ${input.id}::uuid and rc.round_number > 1
    ),
    anc(id) as (
      select parent_id from polity_parent where polity_id = ${input.id}::uuid
      union
      select p.parent_id from polity_parent p join anc on p.polity_id = anc.id
    ),
    bare as materialized (
      select exists (select from polity p where p.id = ${input.id}::uuid and (p.kind = 'region' or (p.kind = 'federal_unit'
        and not exists (select from body where polity_id = ${input.id}::uuid)
        and not exists (select from office where polity_id = ${input.id}::uuid)
        and exists (select from polity_parent pp join slug ps on ps.entity_id = pp.parent_id and ps.is_current and ps.path = 'ca' where pp.polity_id = p.id)))) as yes
    ),
    roots as materialized (
      select c.id, coalesce(c.office_id, c.body_id) as seat, c.office_id is not null as "isOffice",
        c.kind = 'list_allocation' as "isList",
        coalesce((select bp.kind = 'supranational' from body b join polity bp on bp.id = b.polity_id where b.id = c.body_id), false) as "isAbroad",
        e.date_from as date, ${CYCLE} as year, a.k as area,
        case when g.polity_id = ${input.id}::uuid and g.id <> e.id and exists (
            select from later l
            where l.parent = g.id and l.election <> e.id and l.seat = coalesce(c.office_id, c.body_id)
          ) then g.id
          when e.polity_id = ${input.id}::uuid and g.polity_id = e.polity_id and g.kind = e.kind then g.id
          when e.polity_id = ${input.id}::uuid then e.id
          when g.polity_id = ${input.id}::uuid then g.id
          when (select yes from bare) then e.id
          else (
            select n.id from election n
            where n.polity_id = ${input.id}::uuid and n.kind = e.kind
              and extract(year from n.date_from) = extract(year from e.date_from) and not exists (select from entity_merge m where m.entity_id = n.id)
            order by n.parent_id is null desc, abs(n.date_from - e.date_from) limit 1
          ) end as link
      from (
        select x.id from body b join contest x on x.body_id = b.id where b.polity_id = ${input.id}::uuid
        union
        select x.id from body b join contest x on x.body_id = b.id
        where (select yes from bare) and b.polity_id in (select id from anc)
        union
        select x.id from office o join contest x on x.office_id = o.id where o.polity_id = ${input.id}::uuid
        union
        select x.id from election y join contest x on x.election_id = y.id where y.polity_id = ${input.id}::uuid
      ) k
      join contest c on c.id = k.id
      join election e on e.id = c.election_id
      join election g on g.id = coalesce(e.parent_id, e.id)
      left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
      left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
      where c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
        and (e.kind in ('general', 'local') or (
          e.kind = 'primary'
          and not exists (select from contest n where n.previous_round_id = c.id)
          and not exists (
            select from contest n join election ne on ne.id = n.election_id
            where coalesce(n.office_id, n.body_id) = coalesce(c.office_id, c.body_id) and n.seat_id is not distinct from c.seat_id
              and ne.kind in ('general', 'local', 'runoff') and ne.date_from > e.date_from and ne.date_from < e.date_from + 365
          )
          and exists (select from result_set rs join outcome o on o.result_set_id = rs.id where rs.contest_id = c.id and o.kind = 'elected')
        )) and c.kind in ('body_seats', 'list_allocation', 'office')
        and coalesce((select st.voting from seat st where st.id = c.seat_id), true)
        and not exists (
          select from contest pc join election pe on pe.id = pc.election_id
          where pc.id = c.previous_round_id and pe.kind in ('general', 'local')
        )
        and not (c.term = 'remainder' and exists (
          select from contest f where f.election_id = c.election_id and f.term = 'full'
            and f.body_id is not distinct from c.body_id and f.office_id is not distinct from c.office_id
            and f.seat_id is not distinct from c.seat_id and f.district_area_id is not distinct from c.district_area_id
        ))
        and (coalesce(
          (select polity_id from body where id = c.body_id),
          (select polity_id from office where id = c.office_id)
        ) = ${input.id}::uuid or (
          e.polity_id = ${input.id}::uuid and exists (
            select from body b join polity bp on bp.id = b.polity_id
            where b.id = c.body_id and bp.kind = 'supranational'
          )
        ) or (
          (select yes from bare) and c.district_area_id is not null
          and (select polity_id from body where id = c.body_id) in (select id from anc)
          and exists (
            with recursive up(id, depth) as (
              select ap.parent_id, 1 from area_parent ap where ap.area_id = c.district_area_id
              union
              select ap.parent_id, up.depth + 1 from up join area_parent ap on ap.area_id = up.id
              where up.depth < 8
            )
            select from polity_territory t join up on up.id = t.area_id
            where t.polity_id = ${input.id}::uuid and t.valid @> e.date_from
          )
        ))
    ),
    rounds as (
      select r.id as root, r.id from roots r
      union all
      select r.id, c.id from roots r join contest c on c.previous_round_id = r.id
    ),
    tree(root, id) as (
      select root, id from rounds
      union all
      select tree.root, c.id from contest c join tree on c.allocation_parent_id = tree.id
    ),
    winners as materialized (
      select t.root, t.id as contest, o.id, o.party_id, max(coalesce(o.seats, 1)) as seats
      from tree t
      cross join lateral (
        select o.id, o.party_id, o.kind, o.parent_option_id, x.seats
        from contest_option o
        join outcome x on x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
        join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
        where o.contest_id = t.id
        offset 0
      ) o
      where not exists (select from contest k where k.allocation_parent_id = t.id and k.party_scope_id is null offset 0)
        and (t.id = t.root or not exists (select from contest sc where sc.id = t.id and sc.party_scope_id is not null offset 0))
        and not (o.kind = 'list' and ${pooled(sql`o.id`)})
        and not (o.kind = 'candidate' and not ${pooled(sql`o.parent_option_id`)} and exists (
          select from contest_option l
          join outcome lx on lx.option_id = l.id and lx.kind in ('elected', 'won') and lx.candidacy_id is null
          join result_set lr on lr.id = lx.result_set_id and ${latest("lr")}
          where l.contest_id = t.id and l.kind = 'list'
        ))
      group by t.root, t.id, o.id, o.party_id
    ),
    lines as materialized (
      select ch.parent_option_id as parent, ch.party_id, ch.k, ch.contest_id, a.k as area
      from winners w join contest_option ch on ch.contest_id = w.contest and ch.parent_option_id = w.id
      join contest c on c.id = ch.contest_id
      join election e on e.id = c.election_id
      left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
      join area a on a.id = coalesce(c.district_area_id, pt.area_id)
      where w.party_id is null and ch.party_id is not null
    ),
    leads as (
      select distinct on (l.parent) l.parent, l.party_id
      from lines l
      join result_set rs on rs.contest_id = l.contest_id and ${latest("rs")}
      cross join lateral (
        select max(y.value) as value from result_rollup y
        where y.result_set_k = rs.k and y.area_k = l.area and y.option_k = l.k
      ) y
      where y.value is not null
      order by l.parent, y.value desc
    ),
    won as materialized (
      select w.root, w.contest, w.id, w.seats,
        coalesce(bm.into_id, br.parent_party_id, pm.into_id, w.party_id, ld.party_id,
          (select min(cn.party_id::text)::uuid from candidacy cy
            join candidacy_nomination cn on cn.candidacy_id = cy.id and cn.kind = 'nominated'
            where cy.option_id = w.id having count(distinct cn.party_id) = 1),
          (select coalesce(min(cy.member_of_party_id::text)::uuid, o.alliance_id) from contest_option o
            left join candidacy cy on cy.option_id = o.id
            where o.id = w.id and o.alliance_id is not null group by o.alliance_id)) as party,
        exists (select from candidacy cy where cy.option_id = w.id and cy.is_independent)
          and exists (select from contest_option k left join candidacy cy on cy.option_id = k.id
            where k.contest_id in (select w.contest union all select c2.id from contest c0
              join contest c2 on c2.body_id = c0.body_id and c2.election_id = c0.election_id where c0.id = w.contest)
              and (k.party_id is not null or k.alliance_id is not null or cy.member_of_party_id is not null
                or exists (select from candidacy_nomination cn where cn.candidacy_id = cy.id)))
          or exists (select from contest_option o where o.id = w.id and o.party_id is null and o.alliance_id is null
            and o.ballot_label ~* '^(independent|bağımsız|indépendant|unabhängig|indipendent)') as independent,
        case when r."isOffice" then (select coalesce(m.into_id, cy.person_id) from candidacy cy
          left join entity_merge m on m.entity_id = cy.person_id
          where cy.option_id = w.id and cy.person_id is not null order by cy.id limit 1) end as person
      from winners w
      join roots r on r.id = w.root
      left join leads ld on ld.parent = w.id
      left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
      left join party_affiliation br on br.party_id = coalesce(pm.into_id, w.party_id, ld.party_id)
        and br.kind = 'regional_branch' and br.valid @> r.date
      left join entity_merge bm on bm.entity_id = br.parent_party_id
    ),
    fetched as (
      select r.id as root, coalesce(o.parent_option_id, o.id) as top, x.measure, x.value::float8 as value,
        x.count_round, max(x.count_round) over (partition by r.id, x.measure) as first
      from (select id, area from roots where "isOffice") r
      join (select distinct root, contest from won) w on w.root = r.id
      join result_set rs on rs.contest_id = w.contest and ${latest("rs")}
      cross join lateral (
        select x.option_k, x.measure, x.value, x.count_round from result_rollup x
        where x.result_set_k = rs.k and x.area_k = r.area and x.measure = any(${COUNTED}) and x.option_k <> 0
        offset 0
      ) x
      join contest_option o on o.k = x.option_k
    ),
    mains as (
      select root, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main from fetched group by root
    ),
    shares as (
      select f.root,
        sum(f.value) filter (where wn.id is not null) / nullif(sum(f.value), 0) as share
      from fetched f join mains m on m.root = f.root and m.main = f.measure
      left join won wn on wn.root = f.root and wn.id = f.top
      where f.count_round = f.first
      group by f.root
    ),
    seated as (
      select r.seat, bool_or(r."isOffice") as "isOffice", bool_or(r."isAbroad") as "isAbroad", coalesce(r.link::text, r.year::text) as bucket,
        (array_agg(r.link))[1] as link,
        min(r.date) as day, min(r.year) as year, count(distinct r.id)::int as races,
        w.party, bool_or(w.independent) as independent, sum(w.seats)::int as seats, avg(sh.share)::float8 as share,
        case when count(distinct r.id) = 1 then (array_agg(w.person order by w.contest <> w.root, exists (select from contest k where k.id = w.contest and k.allocation_parent_id is not null), w.person))[1] end as winner,
        case when count(distinct r.id) = 1 then (array_agg(w.contest order by w.contest <> w.root, exists (select from contest k where k.id = w.contest and k.allocation_parent_id is not null), w.person))[1] end as decider,
        bool_or(w.contest <> w.root) as runoff,
        coalesce(sum(w.seats) filter (where r."isList"), 0)::int as listed
      from roots r
      join won w on w.root = r.id
      left join shares sh on sh.root = r.id
      group by r.seat, coalesce(r.link::text, r.year::text), w.party
    ),
    sized as (
      select t.*, sum(t.seats) over b as total, sum(t.listed) over b as lists,
        case when (select yes from bare) then sum(t.seats) over b else (select z.seats from body_size z
          where z.body_id = t.seat and (z.valid @> t.day or lower(z.valid) between t.day and t.day + 400)
          order by lower(z.valid) desc limit 1) end as size
      from seated t window b as (partition by t.seat, t.bucket)
    ),
    flagged as (
      select s.*, coalesce(s.total > s.size * 1.02 and s.lists >= s.size * 0.9, false) as "isMixed" from sized s
    )
    select seat, "isOffice", "isAbroad", bucket, link, day::text as date, year, races, party, share, winner,
      decider, coalesce(runoff, false) as "isRunoff",
      party is null and independent as "isIndependent",
      case when "isMixed" then listed else seats end as seats
    from flagged
    where not ("isMixed" and listed = 0)
      and not exists (
        select from election e
        join polity ep on ep.id = e.polity_id and ep.kind = 'supranational'
        where e.id = flagged.link and not exists (select from contest c where c.election_id = e.id)
          and exists (
            select from polity_parent pp
            where pp.parent_id = e.polity_id and pp.valid @> e.date_from
              and not exists (select from election k where k.parent_id = e.id and k.polity_id = pp.polity_id)
          )
      )
    order by day
  `);
  const cabinets = await db.execute<{
    id: string;
    name: string | null;
    head: string | null;
    party: string | null;
    from: string;
    to: string | null;
    isCaretaker: boolean;
  }>(sql`
    select g.id, g.head_person_id as head, g.from_date::text as from, g.to_date::text as to,
      g.caretaker as "isCaretaker",
      (select n.text from name n where n.entity_id = g.id
        order by n.kind = 'short' desc, n.is_preferred desc, n.recorded_from desc, n.id limit 1) as name,
      (select coalesce(pm.into_id, gp.party_id) from government_party gp
        left join entity_merge pm on pm.entity_id = gp.party_id
        where gp.government_id = g.id
        order by gp.role = 'head' desc, gp.seats desc nulls last limit 1) as party
    from government g where g.polity_id = ${input.id}::uuid
    order by g.from_date
  `);
  const seats = [
    ...new Set(
      found.rows
        .filter((row) => input.body == null || row.seat === input.body)
        .map((row) => row.seat),
    ),
  ];
  const parties = [
    ...new Set([
      ...found.rows.flatMap((row) => row.party ?? []),
      ...cabinets.rows.flatMap((row) => row.party ?? []),
    ]),
  ];
  const links = [
    ...new Set(
      found.rows.flatMap((row) => [row.link ?? [], row.decider ?? []].flat()),
    ),
  ];
  const [labels, slugs, colors, positions, homes, own, dated, kinds] =
    await Promise.all([
      names([
        ...seats,
        ...links,
        ...parties,
        ...cabinets.rows.flatMap((row) => row.head ?? []),
        ...found.rows.flatMap((row) => row.winner ?? []),
      ]),
      paths(links),
      refs(parties),
      db.execute<{ party_id: string; lean: number }>(sql`
      select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
      where party_id = any(${sql.param(parties)}::uuid[])
      order by party_id, valid_from desc nulls last
    `),
      db.execute<{ id: string; region: string | null }>(sql`
      select p.id, case when po.iso_code like '%-%' then split_part(po.iso_code, '-', 2) else po.iso_code end as region
      from party p
      join polity po on po.id = p.polity_id
      where p.id = any(${sql.param(parties)}::uuid[])
        and (po.iso_code like '%-%' or po.id <> ${input.id}::uuid)
    `),
      db.execute<{ path: string }>(sql`
      select s.path from slug s join polity p on p.id = s.entity_id
      where s.entity_id = ${input.id}::uuid and s.is_current
        and (p.kind = 'region' or (p.kind = 'federal_unit'
          and not exists (select from body where polity_id = p.id)
          and not exists (select from office where polity_id = p.id)
          and exists (
            select from polity_parent pp join slug ps on ps.entity_id = pp.parent_id and ps.is_current and ps.path = 'ca'
            where pp.polity_id = p.id
          )))
    `),
      db.execute<{
        party: string;
        text: string;
        from: string | null;
        to: string | null;
      }>(sql`
      select n.entity_id::text as party, n.text, lower(n.valid)::text as from, upper(n.valid)::text as to from name n
      where n.entity_id = any(${sql.param(parties)}::uuid[]) and n.kind in ('official', 'english') and not n.is_preferred
        and not (lower_inf(n.valid) and upper_inf(n.valid))
        and exists (select from name p where p.entity_id = n.entity_id and p.kind in ('official', 'english') and p.is_preferred)
    `),
      db.execute<{ id: string; rank: number | null; isOwn: boolean }>(sql`
      select id::text, array_position(array['head_of_state', 'head_of_government', 'governor', 'mayor'], kind::text) as rank,
        polity_id = ${input.id}::uuid as "isOwn"
      from office where id = any(${sql.param(seats)}::uuid[])
      union all
      select id::text, null, polity_id = ${input.id}::uuid from body where id = any(${sql.param(seats)}::uuid[])
    `),
    ]);
  const ranks = new Map(kinds.rows.map((row) => [row.id, row.rank ?? 5]));
  const owned = new Set(
    kinds.rows.filter((row) => row.isOwn).map((row) => row.id),
  );
  const renamed = Map.groupBy(dated.rows, (row) => row.party);
  const lean = new Map(positions.rows.map((row) => [row.party_id, row.lean]));
  const region = new Map(homes.rows.map((row) => [row.id, row.region]));
  const clashes = new Set<string>();
  const bySeat = Map.groupBy(
    found.rows.flatMap((row) =>
      row.party == null ? [] : [{ seat: row.seat, party: row.party }],
    ),
    (row) => row.seat,
  );
  for (const group of bySeat.values()) {
    const texts = Map.groupBy(
      new Set(group.map((row) => row.party)),
      (id) => colors.get(id)?.short ?? colors.get(id)?.label ?? "",
    );
    for (const ids of texts.values()) {
      if (ids.length > 1) {
        const last = new Map(
          ids.map((id) => [
            id,
            Math.max(
              ...found.rows
                .filter((row) => row.party === id)
                .map((row) => row.year),
            ),
          ]),
        );
        const newest = Math.max(...last.values());
        const current = ids.filter((id) => last.get(id) === newest);
        for (const id of ids) {
          if (current.length > 1 || !current.includes(id)) {
            clashes.add(id);
          }
        }
      }
    }
  }
  return {
    scope: own.rows[0]?.path ?? null,
    series: seats
      .map((seat) => {
        const rows = found.rows.filter((row) => row.seat === seat);
        return {
          id: seat,
          name: labels.short.get(seat) ?? labels.full.get(seat) ?? "",
          isOffice: rows.some((row) => row.isOffice),
          isAbroad: rows.some((row) => row.isAbroad),
          isOwn: owned.has(seat),
          points: Map.groupBy(rows, (row) => row.bucket)
            .values()
            .map((group) => {
              const first = group[0];
              let filled = 0;
              for (const row of group) {
                filled += row.seats;
              }
              return {
                name:
                  (first?.link == null ? null : labels.full.get(first.link)) ??
                  `${first?.year ?? ""} ${labels.full.get(seat) ?? ""}`.trim(),
                path:
                  first?.link == null ? null : (slugs.get(first.link) ?? null),
                date: first?.date ?? "",
                year: first?.year ?? 0,
                races: Math.max(...group.map((row) => row.races)),
                filled,
                share:
                  group.length === 1 &&
                  first?.isOffice === true &&
                  first.races === 1
                    ? first.share
                    : null,
                winner:
                  group.length === 1 && first?.winner != null
                    ? (labels.full.get(first.winner) ?? null)
                    : null,
                contest:
                  group.length === 1 && first?.decider != null
                    ? (slugs.get(first.decider) ?? null)
                    : null,
                isRunoff:
                  group.length === 1 && first?.races === 1 && first.isRunoff,
                parties: group
                  .map((row) => ({
                    party: row.party,
                    seats: row.seats,
                    isIndependent: row.isIndependent,
                    label:
                      renamed
                        .get(row.party ?? "")
                        ?.find(
                          (name) =>
                            (name.from == null || name.from <= row.date) &&
                            (name.to == null || row.date < name.to),
                        )?.text ?? null,
                  }))
                  .toSorted(
                    (a, b) =>
                      b.seats - a.seats ||
                      (a.party ?? "").localeCompare(b.party ?? ""),
                  ),
              };
            })
            .toArray()
            .toSorted((a, b) => a.date.localeCompare(b.date))
            .flatMap((point, index, all) => {
              const next = all[index + 1];
              if (
                next != null &&
                next.year - point.year <= 1 &&
                point.filled * 2 < next.filled
              ) {
                next.races += point.races;
                next.filled += point.filled;
                next.share = null;
                next.winner = null;
                next.contest = null;
                next.isRunoff = false;
                const merged = new Map(
                  next.parties.map((entry) => [entry.party, entry.seats]),
                );
                for (const entry of point.parties) {
                  merged.set(
                    entry.party,
                    (merged.get(entry.party) ?? 0) + entry.seats,
                  );
                }
                next.parties = [...merged]
                  .map(([party, seats]) => ({
                    party,
                    seats,
                    isIndependent: false,
                    label:
                      [...next.parties, ...point.parties].find(
                        (entry) => entry.party === party,
                      )?.label ?? null,
                  }))
                  .toSorted((x, y) => y.seats - x.seats);
                return [];
              }
              return [point];
            }),
        };
      })
      .filter((entry) => entry.points.length > (input.body == null ? 1 : 0))
      .toSorted(
        (a, b) =>
          Number(a.isAbroad) - Number(b.isAbroad) ||
          Number(COURT.test(a.name)) - Number(COURT.test(b.name)) ||
          (a.isOffice
            ? a.points.every((point) => point.filled <= 1)
              ? 0
              : 2
            : 1) -
            (b.isOffice
              ? b.points.every((point) => point.filled <= 1)
                ? 0
                : 2
              : 1) ||
          (ranks.get(a.id) ?? 5) - (ranks.get(b.id) ?? 5) ||
          Math.max(...b.points.map((point) => point.filled)) -
            Math.max(...a.points.map((point) => point.filled)) ||
          b.points.length - a.points.length,
      ),
    governments: cabinets.rows.map((row) => ({
      name: row.name ?? "",
      head: row.head == null ? null : (labels.full.get(row.head) ?? null),
      party: row.party,
      from: row.from,
      to: row.to,
      isCaretaker: row.isCaretaker,
    })),
    parties: parties.map((id) => {
      const ref = colors.get(id) ?? { id, label: "", short: null, color: null };
      const text = ref.short ?? ref.label;
      const place = region.get(id);
      const years = found.rows
        .filter((row) => row.party === id)
        .map((row) => row.year);
      const first = Math.min(...years);
      const last = Math.max(...years);
      return {
        ...ref,
        short: !clashes.has(id)
          ? ref.short
          : place != null && place !== ""
            ? `${text} (${place})`
            : years.length === 0
              ? ref.short
              : `${text} (${first === last ? first : `${first}–${last}`})`,
        lean: lean.get(id) ?? null,
      };
    }),
  };
}
export const polities = {
  list: os
    .meta(
      openapi({ method: "GET", path: "/polities", summary: "List polities" }),
    )
    .input(v.optional(v.object({ kinds: v.optional(v.array(v.string())) })))
    .output(
      v.array(
        v.object({
          id: v.pipe(v.string(), v.uuid()),
          kind: v.string(),
          isoCode: v.nullable(v.string()),
          name: v.string(),
          path: v.nullable(v.string()),
          elections: v.number(),
          within: v.number(),
          first: v.nullable(v.number()),
          last: v.nullable(v.number()),
          latest: v.nullable(Link),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const kinds = input?.kinds == null ? null : sql.param(input.kinds);
      const result = await db.execute<{
        id: string;
        kind: string;
        iso_code: string | null;
        elections: number;
        within: number;
        first: number | null;
        last: number | null;
        latest: string | null;
      }>(sql`
        with recursive below(id, top) as (
          select polity_id, parent_id from polity_parent
          union
          select p.polity_id, below.top from polity_parent p join below on p.parent_id = below.id
        ),
        own as materialized (
          select polity_id, count(*)::int as n, min(extract(year from date_from))::int as first,
            max(extract(year from date_from))::int as last
          from election e where not exists (select from entity_merge m where m.entity_id = e.id) group by polity_id
        ),
        nested as materialized (
          select b.top, sum(o.n)::int as n from below b join own o on o.polity_id = b.id group by b.top
        )
        select p.id, p.kind::text, p.iso_code, coalesce(o.n, 0) as elections, coalesce(i.n, 0) as within,
          o.first, o.last,
          case when o.n is not null then (
            select e.id from election e where e.polity_id = p.id and e.status = 'held' and e.date_from <= current_date
              and not exists (select from election x where x.id = e.parent_id and x.polity_id = e.polity_id)
              and not exists (select from entity_merge m where m.entity_id = e.id)
            order by e.kind = 'general' desc, e.date_from desc limit 1
          ) end as latest
        from polity p
        left join own o on o.polity_id = p.id
        left join nested i on i.top = p.id
        where ${kinds}::text[] is null or p.kind::text = any(${kinds}::text[])
      `);
      const ids = result.rows.flatMap((row) =>
        row.latest == null ? [row.id] : [row.id, row.latest],
      );
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      return result.rows
        .map((row) => ({
          id: row.id,
          kind: row.kind,
          isoCode: row.iso_code,
          name: labels.full.get(row.id) ?? "",
          path: slugs.get(row.id) ?? null,
          elections: row.elections,
          within: row.within,
          first: row.first,
          last: row.last,
          latest:
            row.latest == null
              ? null
              : {
                  name: labels.full.get(row.latest) ?? "",
                  path: slugs.get(row.latest) ?? null,
                },
        }))
        .toSorted((a, b) =>
          a.name.localeCompare(b.name, undefined, { numeric: true }),
        );
    }),
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/polities/{id}",
        summary: "Get a polity, its subdivisions, and its elections",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        all: v.optional(
          v.pipe(
            v.union([v.boolean(), v.picklist(["true", "false"])]),
            v.transform((value) => value === true || value === "true"),
          ),
          false,
        ),
      }),
    )
    .output(
      v.object({
        name: v.string(),
        kind: v.string(),
        parent: v.nullable(v.string()),
        breadcrumbs: v.array(Link),
        polities: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            kind: v.string(),
            elections: v.number(),
            first: v.nullable(v.number()),
            last: v.nullable(v.number()),
            population: v.nullable(v.number()),
          }),
        ),
        holdings: v.array(
          v.object({
            level: v.string(),
            total: v.number(),
            complete: v.number(),
            population: v.number(),
            covered: v.number(),
          }),
        ),
        within: v.number(),
        dates: v.number(),
        held: v.number(),
        years: v.array(v.number()),
        results: v.array(
          v.object({
            year: v.nullable(v.number()),
            date: v.string(),
            races: v.array(
              v.object({
                id: v.string(),
                name: v.string(),
                path: v.string(),
                unit: v.nullable(v.string()),
                source: v.nullable(v.string()),
                leaders: v.array(
                  v.object({
                    key: v.string(),
                    label: v.string(),
                    color: v.nullable(v.string()),
                    person: v.nullable(v.string()),
                    party: v.nullable(v.string()),
                    share: v.number(),
                  }),
                ),
              }),
            ),
          }),
        ),
        elections: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            year: v.nullable(v.number()),
            kind: v.string(),
            status: v.string(),
            scope: v.nullable(v.string()),
            lead: v.nullable(v.object({ place: v.string(), more: v.number() })),
          }),
        ),
        mapped: v.nullable(v.string()),
        local: v.nullable(v.string()),
        next: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.string(),
            races: v.number(),
          }),
        ),
        latest: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            scope: v.nullable(v.string()),
            date: v.nullable(v.string()),
            isLocal: v.boolean(),
            races: v.array(
              v.object({
                contest: v.nullable(v.string()),
                first: v.nullable(v.string()),
                pending: v.nullable(
                  v.object({ date: v.string(), isGeneral: v.boolean() }),
                ),
                name: v.string(),
                path: v.nullable(v.string()),
                count: v.number(),
                contested: v.number(),
                isPartyless: v.boolean(),
                measure: v.string(),
                results: v.array(
                  v.object({
                    key: v.string(),
                    label: v.string(),
                    short: v.nullable(v.string()),
                    color: v.nullable(v.string()),
                    person: v.nullable(v.string()),
                    party: v.nullable(v.string()),
                    votes: v.number(),
                    share: v.number(),
                    seats: v.nullable(v.number()),
                  }),
                ),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const [
        found,
        subdivisions,
        electionRows,
        crumbs,
        here,
        mapped,
        holdings,
      ] = await Promise.all([
        db.execute<{
          kind: string;
          parent: string | null;
          isTerritorial: boolean;
        }>(
          sql`
              select p.kind::text, (
                select pp.parent_id from polity_parent pp where pp.polity_id = p.id
                order by upper_inf(pp.valid) desc, upper(pp.valid) desc limit 1
              ) as parent, (p.kind = 'region' or (p.kind = 'federal_unit' and exists (select from polity_parent pp join slug ps on ps.entity_id = pp.parent_id and ps.is_current and ps.path = 'ca' where pp.polity_id = p.id))) as "isTerritorial"
              from polity p where p.id = ${input.id}::uuid
            `,
        ),
        db.execute<{
          id: string;
          kind: string;
          elections: number;
          first: number | null;
          last: number | null;
          population: number | null;
          within: number;
        }>(sql`
            with recursive host as materialized (
              select cg.geom, coalesce(cg.label_point, st_pointonsurface(cg.geom)) as point, cp.parent_id, c.kind = 'county' as "isCounty" from polity c
              join polity_territory ct on ct.polity_id = c.id and upper_inf(ct.valid)
              join area_geometry cg on cg.area_id = ct.area_id and upper_inf(cg.valid)
              join polity_parent cp on cp.polity_id = c.id
              where c.id = ${input.id}::uuid and c.kind in ('county', 'municipality')
            ),
            candidates as materialized (
              select k.id, t.area_id from host
              join polity_parent sp on sp.parent_id = host.parent_id
              join polity k on k.id = sp.polity_id and k.id <> ${input.id}::uuid
                and k.kind in ('municipality', 'school_district', 'special_district') and (host."isCounty" or k.kind <> 'municipality')
              join polity_territory t on t.polity_id = k.id and upper_inf(t.valid)
            ),
            below(id, top, "isNested") as (
              select * from (
                select distinct polity_id, polity_id, true from polity_parent where parent_id = ${input.id}::uuid
                union
                select candidates.id, candidates.id, host."isCounty" from candidates
                join area_geometry g on g.area_id = candidates.area_id and upper_inf(g.valid)
                join host on (host."isCounty" and st_contains(host.geom, coalesce(g.label_point, st_pointonsurface(g.geom))))
                  or (host."isCounty" and g.geom && host.point and st_contains(g.geom, host.point))
                  or (g.geom && host.geom and st_area(st_intersection(g.geom, host.geom)) > 0.1 * least(st_area(g.geom), st_area(host.geom)))
              ) seed
              union
              select p.polity_id, below.top, below."isNested" from polity_parent p join below on p.parent_id = below.id
            )
            select below.top as id, (select kind::text from polity where id = below.top) as kind, count(e.id)::int as elections,
              min(extract(year from e.date_from))::int as first, max(extract(year from e.date_from))::int as last,
              (select max(g.population) from coverage_government g where g.polity_id = below.top and g.kind = 'municipality')::float8 as population,
              sum(case when bool_or(below."isNested") then count(e.id) else 0 end) over ()::int as within
            from below left join lateral (
              select e.id, e.date_from from election e
              where e.polity_id = below.id
                and not exists (select from election p where p.id = e.parent_id and p.polity_id = e.polity_id)
                and not exists (select from entity_merge m where m.entity_id = e.id)
              union
              select e.id, e.date_from
              from (select where below.id = below.top and not exists (select from election x where x.polity_id = below.id)) g
              cross join (select b.id from body b where b.polity_id = below.id) b
              join contest c on c.body_id = b.id
              join election e on e.id = c.election_id
              union
              select e.id, e.date_from
              from (select where below.id = below.top and not exists (select from election x where x.polity_id = below.id)) g
              cross join (select f.id from office f where f.polity_id = below.id) f
              join contest c on c.office_id = f.id
              join election e on e.id = c.election_id
            ) e on true
            group by below.top
          `),
        db.execute<{
          id: string;
          date: string | null;
          precision: string;
          year: number | null;
          kind: string;
          status: string;
          top: string | null;
          parts: number | null;
        }>(sql`
            select e.id, e.date_from::text as date, e.date_precision::text as precision, ${CYCLE} as year, e.kind::text as kind, e.status::text as status,
              case when lead.population is not null then lead.top end as top, lead.parts
            from election e
            left join lateral (
              select ch.polity_id as top, count(*) over ()::int as parts, g.population
              from election ch
              left join lateral (
                select max(g.population) as population from coverage_government g where g.polity_id = ch.polity_id
              ) g on true
              where ch.parent_id = e.id and ch.polity_id <> e.polity_id and not exists (select from entity_merge m where m.entity_id = ch.id)
              order by g.population desc nulls last, ch.polity_id
              limit 1
            ) lead on e.kind = 'local'
            where e.polity_id = ${input.id}::uuid
              and not exists (select from election p where p.id = e.parent_id and p.polity_id = e.polity_id)
              and not exists (select from entity_merge m where m.entity_id = e.id)
            order by e.date_from desc nulls last
          `),
        breadcrumbs(input.id, "all"),
        db.execute<{
          id: string;
          parent: string | null;
          host: string;
          election: string;
          kind: string;
          status: string;
          path: string;
          date: string;
          year: number | null;
          area: string;
          k: number;
          tier: number;
          scoped: boolean;
          isPrimary: boolean;
        }>(sql`
            with recursive up(id) as (
              select parent_id from polity_parent where polity_id = ${input.id}::uuid
              union
              select p.parent_id from polity_parent p join up on p.polity_id = up.id
            ),
            place as (
              select a.id, a.k from polity_territory t join area a on a.id = t.area_id
              join polity p on p.id = t.polity_id and (
                p.kind in ('county', 'municipality', 'region') or not exists (select from election e where e.polity_id = p.id)
              )
              where t.polity_id = ${input.id}::uuid
              order by upper(t.valid) desc nulls first limit 1
            )
            select c.id, c.allocation_parent_id as parent, e.polity_id as host, e.id as election, e.kind::text as kind,
              e.status::text as status, s.path, e.date_from::text as date, ${CYCLE} as year,
              place.id as area, place.k,
              exists (select from polity_territory t where t.polity_id = ${input.id}::uuid and t.valid @> e.date_from) as scoped,
              (c.party_scope_id is not null or e.kind = 'primary') as "isPrimary",
              case
                when c.kind = 'ballot_question' then 5
                when c.kind = 'elector_slate' or o.kind in ('head_of_state', 'head_of_government') then 0
                when (c.district_area_id is null or c.district_area_id in (pt.area_id, place.id)) and (
                  o.kind = 'governor'
                  or b.polity_id in (select id from up) and (b.polity_id <> e.polity_id or c.district_area_id = place.id)
                ) then 1
                when b.polity_id in (select id from up) and b.polity_id <> e.polity_id then 2
                when c.district_area_id is null or c.district_area_id in (pt.area_id, place.id) then 3
                else 4
              end as tier
            from place, election e
            join contest c on c.election_id = e.id and not c.notional
            join slug s on s.entity_id = c.id and s.is_current
            left join body b on b.id = c.body_id
            left join office o on o.id = c.office_id
            left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
            where e.polity_id in (select id from up) and e.date_from is not null and (
              exists (select from election x where x.polity_id = ${input.id}::uuid)
              or not exists (select from polity p where p.id = ${input.id}::uuid and (p.kind = 'region' or (p.kind = 'federal_unit' and exists (select from polity_parent pp join slug ps on ps.entity_id = pp.parent_id and ps.is_current and ps.path = 'ca' where pp.polity_id = p.id))))
              or exists (select from polity_territory t where t.polity_id = ${input.id}::uuid and t.valid @> e.date_from)
            ) and exists (
              select 1 from result_set rs
              where rs.contest_id = c.id and rs.status = 'active' and exists (
                select from result_rollup r where r.result_set_k = rs.k and r.area_k = place.k and r.measure = any(${COUNTED}) offset 0
              ) offset 0
            )
          `),
        db.execute<{ date: string }>(sql`
            select x.date from (
              select e.date_from::text as date, c.id, pt.area_id
              from election e
              join contest c on c.election_id = e.id and (c.allocation_parent_id is null or c.kind = 'elector_slate') and c.party_scope_id is null and not c.notional
              join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
              where e.polity_id = ${input.id}::uuid and e.status = 'held' and e.date_from <= current_date
                and (c.district_area_id is null or c.district_area_id = pt.area_id)
              order by c.kind = 'ballot_question', e.date_from desc
              offset 0
            ) x
            where exists (
              select from result_set rs
              where rs.contest_id = x.id and ${latest("rs")} and exists (
                select from (
                  select u.area_k from result_rollup_unit u where u.result_set_k = rs.k
                  union all
                  select s.area_k from result_rollup_sum s where s.result_set_k = rs.k
                ) r
                join area a on a.k = r.area_k
                where exists (select from area_parent ap where ap.area_id = a.id and ap.parent_id = x.area_id offset 0)
              )
            )
            limit 1
          `),
        db.execute<{
          level: string;
          total: number;
          complete: number;
          population: number;
          covered: number;
        }>(sql`
            select g.level, sum(g.total)::int as total, sum(g.complete)::int as complete,
              sum(g.population_total)::float8 as population, sum(g.population_complete)::float8 as covered
            from polity p join coverage_goal g on g.state = substr(p.iso_code, 4)
            where p.id = ${input.id}::uuid and p.iso_code like 'US-%' and g.goal = 'local' and g.polity = 'us'
              and g.year < extract(year from current_date)
            group by g.level
            order by array_position(array['county', 'municipality', 'township', 'school', 'college', 'special', 'other'], g.level)
          `),
      ]);
      const newest = mapped.rows[0]?.date;
      const ranked =
        newest == null
          ? null
          : await db.execute<{ id: string }>(sql`
            select c.id
            from election e
            join contest c on c.election_id = e.id and (c.allocation_parent_id is null or c.kind = 'elector_slate') and c.party_scope_id is null and not c.notional
            join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
            join area a on a.id = pt.area_id
            cross join lateral (
              select sum(r.value) as total from result_set rs cross join lateral (
                select r.value from result_rollup r
                where r.result_set_k = rs.k and r.area_k = a.k and r.measure = any(${COUNTED}) offset 0
              ) r
              where rs.contest_id = c.id and ${latest("rs")}
            ) v
            where e.polity_id = ${input.id}::uuid and e.status = 'held' and e.date_from = ${newest}::date
              and (c.district_area_id is null or c.district_area_id = pt.area_id)
            order by c.kind <> 'elector_slate', c.kind = 'ballot_question', c.term is not distinct from 'remainder', v.total desc nulls last
            limit 1
          `);
      const polity = found.rows[0];
      if (polity == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const voted =
        electionRows.rows.length > 0 && polity.kind !== "region"
          ? []
          : Map.groupBy(here.rows, (row) => row.election)
              .values()
              .flatMap((rows) => rows.slice(0, 1))
              .toArray()
              .toSorted((a, b) => b.date.localeCompare(a.date));
      const ids = [
        input.id,
        ...subdivisions.rows.map((row) => row.id),
        ...electionRows.rows.flatMap((row) =>
          row.top == null ? [row.id] : [row.id, row.top],
        ),
        ...voted.map((row) => row.election),
      ];
      const today = new Date().toISOString().slice(0, 10);
      const held = electionRows.rows.filter(
        (row) => row.date != null && row.date <= today && row.status === "held",
      );
      const fallback = voted.find(
        (row) =>
          polity.isTerritorial &&
          row.kind === "general" &&
          row.status === "held" &&
          row.date <= today,
      );
      const recent =
        ((held[0]?.kind === "local" || held[0]?.kind === "runoff") &&
        ![
          "sovereign_state",
          "supranational",
          "federal_unit",
          "region",
        ].includes(polity.kind)
          ? held[0]
          : held.find(
              (row) =>
                row.kind === "general" &&
                Date.parse(held[0]?.date ?? "") - Date.parse(row.date ?? "") <
                  9e10,
            )) ??
        held[0] ??
        (fallback == null
          ? null
          : { id: fallback.election, date: fallback.date });
      const isBorrowed = held.length === 0 && polity.isTerritorial;
      const upcoming = electionRows.rows.findLast(
        (row) =>
          row.date != null && row.date >= today && row.status === "scheduled",
      );
      const parents = new Set(here.rows.map((row) => row.parent));
      const kept = here.rows.filter((row) => !parents.has(row.id));
      const sizes = Map.groupBy(kept, (row) => row.date)
        .entries()
        .map(([day, rows]) => [day, rows.length] as const)
        .toArray()
        .toSorted(([a], [b]) => b.localeCompare(a));
      const shown = new Set<string>();
      let total = 0;
      for (const [day, size] of sizes) {
        if (!input.all && shown.size > 0 && total + size > 16 && total >= 8) {
          break;
        }
        shown.add(day);
        total += size;
      }
      const general = kept
        .filter((row) => !row.isPrimary && row.tier <= 1)
        .toSorted((a, b) => b.date.localeCompare(a.date))[0];
      if (
        general != null &&
        !kept.some(
          (row) => !row.isPrimary && row.tier <= 1 && shown.has(row.date),
        )
      ) {
        shown.add(general.date);
      }
      const [counted, labels, slugs, summed, built] = await Promise.all([
        upcoming == null
          ? null
          : db.execute<{ count: number }>(sql`
            select count(*)::int as count from contest c
            where c.election_id = ${upcoming.id}::uuid and c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
          `),
        names(ids),
        paths(ids),
        recent == null
          ? []
          : races(recent.id, input.id, recent.date, true, held.length === 0),
        Promise.all(
          kept
            .filter((row) => shown.has(row.date))
            .map(async (row) => {
              const other = await find(row.id);
              if (other.place == null) {
                return null;
              }
              const kids =
                other.place.k === row.k
                  ? []
                  : await children(row.id, other.place, true, row.date);
              const direct = kids.find((kid) => kid.k === row.k);
              const route =
                direct != null || other.place.k === row.k
                  ? null
                  : await db.execute<{ path: Array<string> }>(sql`
                    with recursive up(id, path) as (
                      select ${row.area}::uuid, array[]::uuid[]
                      union all
                      select p.parent_id, p.area_id || up.path from up
                      join area_parent p on p.area_id = up.id and p.valid @> ${row.date}::date
                      where up.id <> ${other.place.id}::uuid and cardinality(up.path) < 4
                    )
                    select to_json(path) as path from up where id = ${other.place.id}::uuid limit 1
                  `);
              const steps = direct == null ? [] : [direct.segment];
              const below = route?.rows[0]?.path ?? [];
              let current = other.place;
              for (const id of below) {
                const step = (
                  steps.length === 0
                    ? kids
                    : await children(row.id, current, false, row.date)
                ).find((kid) => kid.id === id);
                if (step == null) {
                  return null;
                }
                steps.push(step.segment);
                current = step;
              }
              if (other.place.k !== row.k && steps.length === 0) {
                return null;
              }
              const unit = steps.join("/");
              const [totals, label, lineage] = await Promise.all([
                load([row.id], [row.k], row.date, [row.id]),
                names([row.id], row.date),
                branches(row.date),
              ]);
              const loaded = totals.get(row.k);
              return {
                ...row,
                unit,
                valid: loaded?.side.valid ?? 0,
                source: loaded?.sets.some((set) => set.isDefault)
                  ? null
                  : (loaded?.sets[0]?.source.publisher ?? null),
                name: label.full.get(row.id) ?? "",
                path: [row.path, unit].filter((part) => part !== "").join("/"),
                leaders:
                  loaded == null
                    ? []
                    : compare(loaded.side, null, lineage, "party")
                        .flatMap((entry) =>
                          entry.current == null
                            ? []
                            : [
                                {
                                  key: entry.key,
                                  label:
                                    entry.current.person == null
                                      ? (entry.short ?? entry.label)
                                      : entry.label,
                                  color: entry.color,
                                  person: entry.current.person,
                                  party: entry.current.party,
                                  votes: entry.current.votes,
                                  share: entry.current.share,
                                },
                              ],
                        )
                        .toSorted((a, b) => b.votes - a.votes)
                        .slice(0, 2),
              };
            }),
        ),
      ]);
      const raced = summed.filter((race) => !race.isFolded);
      const tiers =
        Map.groupBy(
          raced.filter(
            (race) =>
              race.body != null &&
              (race.round ?? 1) === 1 &&
              (race.kind === "body_seats" || race.kind === "list_allocation"),
          ),
          (race) => race.body,
        )
          .values()
          .find((group) => {
            let seats = 0;
            for (const race of group) {
              for (const row of race.results) {
                seats += row.seats ?? 0;
              }
            }
            return (
              new Set(group.map((race) => race.kind)).size === 2 &&
              (group[0]?.size == null || seats <= group[0].size * 1.02)
            );
          }) ?? [];
      const seated = tiers.find((race) => race.kind === "body_seats");
      const local =
        built
          .filter((row) => row != null)
          .toSorted(
            (a, b) =>
              Number(a.isPrimary) - Number(b.isPrimary) ||
              Number(a.tier > 1) - Number(b.tier > 1) ||
              b.date.localeCompare(a.date) ||
              a.tier - b.tier,
          )[0]?.id ?? null;
      return {
        name: labels.full.get(input.id) ?? "",
        kind: polity.kind,
        parent: polity.parent,
        breadcrumbs: crumbs.slice(0, -1),
        polities: subdivisions.rows
          .map((row) => ({
            name: labels.full.get(row.id) ?? "",
            path: slugs.get(row.id) ?? null,
            kind: row.kind,
            elections: row.elections,
            first: row.first,
            last: row.last,
            population: row.population,
          }))
          .toSorted((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true }),
          ),
        holdings: holdings.rows.filter((row) => row.population > 0),
        within: subdivisions.rows[0]?.within ?? 0,
        dates: sizes.length,
        held:
          electionRows.rows.length +
          new Set(here.rows.map((row) => `${row.host}:${row.date}`)).size,
        years: [
          ...new Set(
            kept.flatMap((row) => (row.year == null ? [] : [row.year])),
          ),
        ],
        results: Map.groupBy(
          built
            .filter((race) => race != null)
            .filter((race) => race.leaders.length > 0)
            .toSorted(
              (a, b) =>
                b.date.localeCompare(a.date) ||
                a.tier - b.tier ||
                b.valid - a.valid ||
                a.name.localeCompare(b.name, undefined, { numeric: true }),
            ),
          (race) => race.date,
        )
          .entries()
          .map(([date, entries]) => ({
            date,
            year: entries[0]?.year ?? null,
            races: entries.map((race) => ({
              id: race.id,
              name: race.name,
              path: race.path,
              unit: race.unit === "" ? null : race.unit,
              source: race.source,
              leaders: race.leaders,
            })),
          }))
          .toArray(),
        elections: [
          ...electionRows.rows.map((row) => ({
            name: labels.full.get(row.id) ?? "",
            path: slugs.get(row.id) ?? null,
            date:
              row.precision === "day"
                ? row.date
                : (row.date?.slice(0, row.precision === "month" ? 7 : 4) ??
                  null),
            year: row.year,
            kind: row.kind,
            status: row.status,
            scope: null,
            rank: 0,
            lead:
              row.top == null
                ? null
                : {
                    place: labels.full.get(row.top) ?? "",
                    more: (row.parts ?? 1) - 1,
                  },
          })),
          ...voted.map((row) => {
            const isScoped =
              polity.isTerritorial &&
              row.scoped &&
              (row.kind === "general" || row.kind === "referendum");
            return {
              name: labels.full.get(row.election) ?? "",
              path: slugs.get(row.election) ?? null,
              date: row.date,
              year: row.year,
              kind: row.kind,
              status: row.status,
              scope: isScoped ? (slugs.get(input.id) ?? null) : null,
              rank: isScoped
                ? row.kind === "general"
                  ? 1
                  : 2
                : row.kind === "by_election"
                  ? 3
                  : 4,
              lead: null,
            };
          }),
        ]
          .toSorted(
            (a, b) =>
              (b.year ?? 0) - (a.year ?? 0) ||
              a.rank - b.rank ||
              (b.date ?? "").localeCompare(a.date ?? ""),
          )
          .map(({ rank: _rank, ...entry }) => entry),
        mapped: ranked?.rows[0]?.id ?? null,
        local,
        next:
          upcoming?.date == null
            ? null
            : {
                name: labels.full.get(upcoming.id) ?? "",
                path: slugs.get(upcoming.id) ?? null,
                date: upcoming.date,
                races: counted?.rows[0]?.count ?? 0,
              },
        latest:
          recent == null
            ? null
            : {
                name: labels.full.get(recent.id) ?? "",
                path: slugs.get(recent.id) ?? null,
                scope: isBorrowed ? (slugs.get(input.id) ?? null) : null,
                date: recent.date,
                isLocal:
                  !isBorrowed &&
                  polity.kind !== "region" &&
                  (sizes[0]?.[0] ?? "") > (recent.date ?? ""),
                races: (seated == null
                  ? raced
                  : [
                      {
                        ...seated,
                        path: null,
                        results: Map.groupBy(
                          tiers
                            .toSorted(
                              (a, b) =>
                                Number(b.kind === "list_allocation") -
                                Number(a.kind === "list_allocation"),
                            )
                            .flatMap((race) => race.results),
                          (row) => row.key,
                        )
                          .values()
                          .flatMap(([head, ...rest]) => {
                            if (head == null) {
                              return [];
                            }
                            let seats = head.seats ?? 0;
                            for (const row of rest) {
                              seats += row.seats ?? 0;
                            }
                            return [{ ...head, seats }];
                          })
                          .toArray(),
                      },
                      ...raced.filter((race) => !tiers.includes(race)),
                    ]
                )
                  .toSorted(
                    (a, b) =>
                      Number(COURT.test(a.name)) - Number(COURT.test(b.name)),
                  )
                  .map((race, _index, all) => {
                    const namesakes = all.filter(
                      (other) => other !== race && other.name === race.name,
                    );
                    const isOffice = race.kind === "office" && race.count <= 1;
                    let contested = 0;
                    for (const row of race.results) {
                      contested += isOffice ? 0 : (row.seats ?? 0);
                    }
                    return {
                      contest: race.contest,
                      first: race.first?.path ?? null,
                      pending:
                        race.pending == null
                          ? null
                          : {
                              date: race.pending.date,
                              isGeneral: race.pending.isGeneral,
                            },
                      name:
                        namesakes.length === 0
                          ? race.name
                          : race.count > 1 &&
                              namesakes.some(
                                (other) =>
                                  (other.round ?? 1) !== (race.round ?? 1),
                              )
                            ? m.race_round({
                                name: race.name,
                                round: race.round ?? 1,
                              })
                            : race.kind === "list_allocation" ||
                                race.noun === ""
                              ? race.name
                              : m.race_split({
                                  name: race.name,
                                  noun: race.noun,
                                }),
                      path: race.path,
                      count: race.count,
                      contested,
                      isPartyless:
                        contested > 1 &&
                        race.results.every(
                          (row) =>
                            row.key.startsWith("kind:") ||
                            (row.party == null &&
                              row.person != null &&
                              !row.isIndependent),
                        ),
                      measure: race.measure,
                      results: (isOffice
                        ? race.results
                            .map((row) => ({ ...row, seats: null }))
                            .toSorted((a, b) => b.votes - a.votes)
                        : contested > 0
                          ? race.results.toSorted(
                              (a, b) => (b.seats ?? 0) - (a.seats ?? 0),
                            )
                          : race.results
                      )
                        .filter(
                          (row) =>
                            row.votes > 0 ||
                            (row.seats ?? 0) > 0 ||
                            race.results.every((other) => other.votes === 0),
                        )
                        .slice(0, 3),
                    };
                  })
                  .filter((race) => race.results.length > 0),
              },
      };
    }),
  neighbors: os
    .meta(
      openapi({
        method: "GET",
        path: "/polities/{id}/neighbors",
        summary:
          "List a polity's sibling polities that share its boundary scheme",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        on: v.optional(v.pipe(v.string(), v.isoDate())),
      }),
    )
    .output(
      v.array(
        v.object({
          k: v.number(),
          name: v.string(),
          path: v.string(),
          isAdjacent: v.boolean(),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const day = input.on ?? new Date().toISOString().slice(0, 10);
      const found = await db.execute<{
        id: string;
        k: number;
        isAdjacent: boolean;
      }>(sql`
        with me as (
          select a.id, a.scheme_code from polity_territory t join area a on a.id = t.area_id
          where t.polity_id = ${input.id}::uuid
          order by t.valid @> ${day}::date desc, upper(t.valid) desc nulls first limit 1
        ),
        up as (
          select parent_id from polity_parent where polity_id = ${input.id}::uuid
          order by valid @> ${day}::date desc, upper_inf(valid) desc, upper(valid) desc limit 1
        )
        select distinct on (p.polity_id) p.polity_id as id, a.k,
          exists (
            select from area_geometry g join area_geometry o on o.area_id = (select id from me)
            where g.area_id = a.id and g.valid @> ${day}::date and o.valid @> ${day}::date
              and st_dwithin(g.geom, o.geom, 0.002)
          ) as "isAdjacent"
        from polity_parent p
        join polity_territory t on t.polity_id = p.polity_id
        join area a on a.id = t.area_id and a.scheme_code = (select scheme_code from me)
        where p.parent_id = (select parent_id from up) and p.polity_id <> ${input.id}::uuid
          and p.valid @> ${day}::date
        order by p.polity_id, t.valid @> ${day}::date desc, upper(t.valid) desc nulls first
      `);
      const ids = found.rows.map((row) => row.id);
      const [labels, slugs] = await Promise.all([names(ids, day), paths(ids)]);
      return found.rows.flatMap((row) => {
        const path = slugs.get(row.id);
        return path == null
          ? []
          : [
              {
                k: row.k,
                name: labels.full.get(row.id) ?? "",
                path,
                isAdjacent: row.isAdjacent,
              },
            ];
      });
    }),
  record: os
    .meta(
      openapi({
        method: "GET",
        path: "/polities/{id}/record",
        summary:
          "List every general election of a polity's own chambers and offices, with the seats each party won and each office's winner and share",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        body: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.object({
        scope: v.nullable(v.string()),
        series: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            isOffice: v.boolean(),
            isAbroad: v.boolean(),
            isOwn: v.boolean(),
            points: v.array(
              v.object({
                name: v.string(),
                path: v.nullable(v.string()),
                date: v.string(),
                year: v.number(),
                races: v.number(),
                filled: v.number(),
                share: v.nullable(v.number()),
                winner: v.nullable(v.string()),
                contest: v.nullable(v.string()),
                isRunoff: v.boolean(),
                parties: v.array(
                  v.object({
                    party: v.nullable(v.string()),
                    seats: v.number(),
                    isIndependent: v.optional(v.boolean()),
                    label: v.nullable(v.string()),
                  }),
                ),
              }),
            ),
          }),
        ),
        governments: v.array(
          v.object({
            name: v.string(),
            head: v.nullable(v.string()),
            party: v.nullable(v.string()),
            from: v.string(),
            to: v.nullable(v.string()),
            isCaretaker: v.boolean(),
          }),
        ),
        parties: v.array(
          v.object({
            id: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
            lean: v.nullable(v.number()),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      if (input.body == null) {
        const stored = await db.execute<{
          record: Awaited<ReturnType<typeof record>>;
        }>(sql`
          select record from polity_record where polity_id = ${input.id}::uuid and built_at > now() - interval '8 hours'
        `);
        const row = stored.rows[0];
        if (row != null) {
          return row.record;
        }
      }
      return record(input);
    }),
  holders: os
    .meta(
      openapi({
        method: "GET",
        path: "/polities/{id}/holders",
        summary:
          "List who holds each of a polity's own offices and council seats, judgeships aside: the winners of the latest election we hold for each, or a later acting, appointed, or trustee holder from tenures, with vacant and unrecorded seats marked",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        groups: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            isBody: v.boolean(),
            isFormer: v.boolean(),
            seats: v.nullable(v.number()),
            rows: v.array(
              v.object({
                label: v.nullable(v.string()),
                person: v.nullable(v.string()),
                name: v.nullable(v.string()),
                party: v.nullable(v.string()),
                photo: Photo,
                via: v.string(),
                since: v.nullable(v.string()),
                contest: v.nullable(v.string()),
                isStale: v.boolean(),
                due: v.nullable(v.number()),
                elected: v.nullable(
                  v.object({
                    person: v.nullable(v.string()),
                    name: v.nullable(v.string()),
                    date: v.string(),
                  }),
                ),
                next: v.nullable(
                  v.object({
                    person: v.nullable(v.string()),
                    name: v.nullable(v.string()),
                    date: v.string(),
                    contest: v.nullable(v.string()),
                  }),
                ),
              }),
            ),
          }),
        ),
        parties: v.array(
          v.object({
            id: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        owner: string;
        kind: string | null;
        label: string | null;
        person: string | null;
        party: string | null;
        via: string;
        since: string | null;
        contest: string | null;
        isStale: boolean;
        electedPerson: string | null;
        electedDate: string | null;
        nextPerson: string | null;
        nextDate: string | null;
        nextContest: string | null;
        due: number | null;
        labels: Array<string> | null;
        size: number | null;
      }>(sql`
        with own as (
          select area_id from polity_territory where polity_id = ${input.id}::uuid
        ),
        roots as materialized (
          select c.id, c.seat_id, coalesce(c.office_id, c.body_id) as owner, c.office_id is not null as "isOffice",
            c.kind = 'list_allocation' as "isList",
            c.kind <> 'list_allocation' or exists (
              select from result_set rs join outcome x on x.result_set_id = rs.id
              where rs.contest_id = c.id and x.candidacy_id is not null and x.kind in ('elected', 'won')
            ) as "isNamed",
            e.date_from as date, e.kind::text as kind, coalesce(c.magnitude, 1) as magnitude,
            case when n.text ~* '\\mspecial\\M' then 'special' else coalesce((select pe.kind::text from contest pc join election pe on pe.id = pc.election_id where pc.id = c.previous_round_id), e.kind::text) end as "firstKind",
            coalesce((select pe.date_from from contest pc join election pe on pe.id = pc.election_id where pc.id = c.previous_round_id), e.date_from) as "firstDate",
            case when c.district_area_id in (select area_id from own) then null else c.district_area_id end as district,
            (select a.k from area a where a.id = coalesce(c.district_area_id, (
              select pt.area_id from polity_territory pt where pt.polity_id = e.polity_id and pt.valid @> e.date_from limit 1
            ))) as place,
            substring(n.text from '(?:election|elections|runoff|runoffs|primary)(?:, (.+))$') as suffix,
            e.kind not in ('special', 'by_election') and extract(year from e.date_from) = extract(year from current_date)
              and exists (select from slug s where s.entity_id = ${input.id}::uuid and s.is_current and s.path like 'us/%') as "isElect"
          from contest c
          join election e on e.id = c.election_id
          left join name n on n.entity_id = c.id and n.is_preferred
          where c.allocation_parent_id is null and c.party_scope_id is null and not c.notional
            and c.kind in ('office', 'body_seats', 'list_allocation') and e.date_from <= current_date
            and e.kind in ('general', 'local', 'runoff', 'special', 'by_election', 'primary')
            and not exists (select from contest later where later.previous_round_id = c.id)
            and not exists (select from entity_merge m where m.entity_id = c.id)
            and (c.office_id in (select id from office where polity_id = ${input.id}::uuid)
              or c.body_id in (select id from body where polity_id = ${input.id}::uuid))
        ),
        labeled as (
          select r.*,
            coalesce((select st.label from seat st where st.id = r.seat_id),
              (select regexp_replace(dn.text, '^.*, ', '') from name dn where dn.entity_id = r.district and dn.is_preferred limit 1),
              case when r.suffix !~* '^(runoff|(first|second|third|final) round|round \\d+)$' then r.suffix end) as label
          from roots r
        ),
        keyed as materialized (
          select l.*, l.owner::text || ':' || coalesce(lower(l.label), l.seat_id::text, '') as post from labeled l
        ),
        won as materialized (
          select k.*, w.option, w.person, w.party
          from keyed k
          cross join lateral (
            select o.id as option, coalesce(m.into_id, xc.person_id, cy.person_id) as person, coalesce(pm.into_id, o.party_id, xc.member_of_party_id, cy.member_of_party_id, (
                select ch.party_id from contest_option ch
                cross join lateral (
                  select sum(y.value) as value from result_rollup y
                  where y.result_set_k = rs.k and y.area_k = k.place and y.option_k = ch.k
                    and y.measure = 1 and y.count_round = 0 offset 0
                ) y
                where ch.parent_option_id = o.id and ch.party_id is not null and y.value is not null
                order by y.value desc limit 1
              )) as party
            from result_set rs
            join outcome x on x.result_set_id = rs.id and x.kind in ('elected', 'won') and (x.candidacy_id is not null) = k."isList"
            join contest_option o on o.id = x.option_id
            left join candidacy xc on xc.id = x.candidacy_id
            left join lateral (
              select cy.person_id, cy.member_of_party_id from candidacy cy where cy.option_id = o.id and cy.person_id is not null order by cy.id limit 1
            ) cy on true
            left join entity_merge m on m.entity_id = coalesce(xc.person_id, cy.person_id)
            left join entity_merge pm on pm.entity_id = o.party_id
            where rs.contest_id = k.id and ${latest("rs")} and (not k."isList" or xc.person_id is not null)
          ) w
        ),
        last as (
          select post, max(date) as date, bool_or(magnitude > 1) as multi from won where not "isElect" group by post
        ),
        terms as (
          select post, max(gap) as days
          from (
            select post, gap, row_number() over (partition by post order by date desc) as at
            from (
              select post, date, date - lag(date) over (partition by post order by date) as gap
              from (select distinct post, date from won where not "isElect") d
            ) g
            where gap >= 300
          ) r
          where at <= 3
          group by post
        ),
        ran as (
          select distinct k.post, coalesce(m.into_id, cy.person_id) as person
          from keyed k
          join last l on l.post = k.post and l.multi and k.date = l.date
          join contest_option o on o.contest_id = k.id
          join candidacy cy on cy.option_id = o.id and cy.person_id is not null
          left join entity_merge m on m.entity_id = cy.person_id
        ),
        renewed as (
          select l.post,
            (select count(*) from won w where w.post = l.post and w.date = l.date) >= (
              select count(*) from won w where w.post = l.post and w.date = (
                select max(p.date) from won p where p.post = l.post and not p."isElect" and p.date < l.date
              )
            ) and exists (
              select from won w join ran r on r.post = w.post and r.person = w.person
              where w.post = l.post and w.date < l.date and w.date > l.date - interval '42 months'
            ) as yes
          from last l where l.multi
        ),
        held as (
          select distinct on (w.post, coalesce(w.person::text, w.option::text)) w.post, w.owner, w."isOffice", w.label, w.person, w.party, w.date, w.id as contest
          from won w join last l on l.post = w.post left join renewed rn on rn.post = w.post
          where not w."isElect" and (w.date = l.date or (
            l.multi and not coalesce(rn.yes, false) and w.date > l.date - interval '42 months'
            and not exists (select from ran r where r.post = w.post and r.person = w.person)
          ))
          order by w.post, coalesce(w.person::text, w.option::text), w.date desc
        ),
        newest as (
          select owner, max(date) as date from keyed group by owner
        ),
        posts as (
          select distinct on (post) post, owner, "isOffice", label from keyed order by post, date desc
        ),
        pending as (
          select distinct on (k.post) k.post, k.id, k.date
          from keyed k left join last l on l.post = k.post
          where k.kind <> 'primary' and not k."isElect" and k."isNamed" and (l.date is null or k.date > l.date)
            and not exists (select from won w where w.id = k.id)
          order by k.post, k.date desc
        ),
        elects as (
          select distinct on (w.post) w.post, w.person, w.date, w.id from won w where w."isElect" order by w.post, w.date desc
        ),
        tenured as (
          select distinct on (t.office_id) t.office_id, coalesce(m.into_id, t.person_id) as person, t.acquired_via::text as via, t.from_date
          from tenure t left join entity_merge m on m.entity_id = t.person_id
          where t.office_id in (select owner from posts where "isOffice") and (t.to_date is null or t.to_date > current_date)
          order by t.office_id, t.from_date desc
        ),
        successors as materialized (
          select f.body_id, f.person_id as before, coalesce(m.into_id, t.person_id) as person, t.acquired_via::text as via, t.from_date
          from tenure t
          join tenure f on f.id = t.succeeds_tenure_id
          left join entity_merge m on m.entity_id = t.person_id
          where t.body_id in (select owner from posts where not "isOffice") and (t.to_date is null or t.to_date > current_date)
        ),
        ended as (
          select distinct on (t.office_id) t.office_id, t.to_date
          from tenure t
          where t.office_id in (select owner from posts where "isOffice") and t.to_date <= current_date
          order by t.office_id, t.to_date desc
        ),
        cycles as (
          select distinct on (p.owner) p.owner, round(g.gap / 365.25)::int as years
          from (
            select post, date, date - lag(date) over (partition by post order by date) as gap
            from (select distinct post, "firstDate" as date from won where not "isElect" and "firstKind" not in ('special', 'by_election')) d
          ) g
          join posts p on p.post = g.post
          where g.gap >= 300 and exists (select from slug s where s.entity_id = ${input.id}::uuid and s.is_current and (s.path = 'us' or s.path like 'us/%'))
          group by p.owner, round(g.gap / 365.25)
          order by p.owner, count(*) desc, max(g.date) desc
        ),
        bases as (
          select w.post, max(w."firstDate") as date, cy.years,
            mode() within group (order by extract(year from w."firstDate")::int % cy.years) as residue
          from won w join posts p on p.post = w.post join cycles cy on cy.owner = p.owner
          where not w."isElect" and w."firstKind" not in ('special', 'by_election') and cy.years > 0
          group by w.post, cy.years
        ),
        owned as (
          select owner, array_agg(distinct label) filter (where label is not null) as labels,
            (select z.seats from body_size z where z.body_id = owner and z.valid @> current_date limit 1) as size
          from keyed group by owner
        )
        select p.owner::text, (select kind::text from office where id = p.owner) as kind, p.label,
          case when pd.id is not null then null when tn.person is not null and (h.date is null or tn.from_date > h.date) then tn.person
            when en.to_date is not null and en.to_date > h.date and tn.person is null then null else h.person end::text as person,
          case when pd.id is null and (tn.person is null or (h.date is not null and tn.from_date <= h.date) or rp.via = 'list_replacement')
            and not (en.to_date is not null and en.to_date > h.date and tn.person is null) then h.party end::text as party,
          case when pd.id is not null then 'unknown' when tn.person is not null and (h.date is null or tn.from_date > h.date) then tn.via
            when en.to_date is not null and en.to_date > h.date and tn.person is null then 'vacant' else 'elected' end as via,
          case when pd.id is not null then pd.date when tn.person is not null and (h.date is null or tn.from_date > h.date) then tn.from_date
            when en.to_date is not null and en.to_date > h.date and tn.person is null then en.to_date else h.date end::text as since,
          case when pd.id is not null then pd.id when tn.person is not null and (h.date is null or tn.from_date > h.date) then null
            when en.to_date is not null and en.to_date > h.date and tn.person is null then null else h.contest end::text as contest,
          pd.id is null and el.post is null and h.date is not null and h.date + least(greatest(coalesce(tr.days, 1461), 700), 2200) + 120 < current_date
            and not (tn.person is not null and tn.from_date > h.date) as "isStale",
          case when pd.id is null and ((tn.person is not null and tn.from_date > h.date) or (en.to_date > h.date and tn.person is null)) then h.person end::text as "electedPerson",
          case when pd.id is null and ((tn.person is not null and tn.from_date > h.date) or (en.to_date > h.date and tn.person is null)) then h.date end::text as "electedDate",
          el.person::text as "nextPerson", el.date::text as "nextDate", el.id::text as "nextContest",
          case when pd.id is null and bs.date + make_interval(years => bs.years) > current_date then extract(year from bs.date)::int + bs.years
            - ((extract(year from bs.date)::int + bs.years - bs.residue) % bs.years + bs.years) % bs.years end as due,
          od.labels, od.size
        from posts p
        left join bases bs on bs.post = p.post
        join owned od on od.owner = p.owner
        left join held h on h.post = p.post
        left join pending pd on pd.post = p.post
        left join terms tr on tr.post = p.post
        left join elects el on el.post = p.post
        left join tenured tf on p."isOffice" and tf.office_id = p.owner
        left join lateral (
          select sc.person, sc.via, sc.from_date from successors sc
          where not p."isOffice" and sc.body_id = p.owner and sc.before = h.person and sc.from_date > h.date
          order by sc.from_date desc limit 1
        ) rp on true
        cross join lateral (
          select coalesce(tf.person, rp.person) as person, coalesce(tf.via, rp.via) as via,
            coalesce(tf.from_date, rp.from_date) as from_date
        ) tn
        left join ended en on p."isOffice" and en.office_id = p.owner and h.person is not null
        join newest nw on nw.owner = p.owner
        where (h.post is not null or pd.post is not null or (p."isOffice" and tn.person is not null))
          and coalesce(pd.date, h.date, tn.from_date) > nw.date - interval '6 years'
      `);
      const owners = [...new Set(found.rows.map((row) => row.owner))];
      const people = found.rows.flatMap((row) =>
        [row.person, row.electedPerson, row.nextPerson].filter(
          (id) => id != null,
        ),
      );
      const parties = [
        ...new Set(found.rows.flatMap((row) => row.party ?? [])),
      ];
      const [labels, slugs, colors, faces] = await Promise.all([
        names([...owners, ...people]),
        paths(
          found.rows.flatMap((row) =>
            [row.contest, row.nextContest].filter((id) => id != null),
          ),
        ),
        refs(parties),
        portraits(found.rows.flatMap((row) => row.person ?? [])),
      ]);
      const year = new Date().getFullYear();
      const parse = (label: string | null) => {
        const text = (label ?? "").toLowerCase().trim();
        const match = /^(.*?)[\s-]+(\d+|[ivxlcdm]+|[a-z])$/u.exec(text);
        const tail = match?.[2];
        if (tail == null) {
          return { scheme: text, seat: null, isRoman: false };
        }
        if (!/^[ivxlcdm]+$/u.test(tail)) {
          return {
            scheme: match?.[1] ?? "",
            seat: /^\d+$/u.test(tail) ? String(Number(tail)) : tail,
            isRoman: false,
          };
        }
        const ROMAN: Record<string, number> = {
          i: 1,
          v: 5,
          x: 10,
          l: 50,
          c: 100,
          d: 500,
          m: 1000,
        };
        let value = 0;
        for (const [at, char] of [...tail].entries()) {
          const digit = ROMAN[char] ?? 0;
          value += digit < (ROMAN[tail[at + 1] ?? ""] ?? 0) ? -digit : digit;
        }
        return { scheme: match?.[1] ?? "", seat: String(value), isRoman: true };
      };
      const groups = Map.groupBy(found.rows, (row) => row.owner)
        .entries()
        .filter(([id, rows]) => {
          const name = labels.full.get(id) ?? "";
          return (
            rows[0]?.kind == null ||
            !COURT.test(name) ||
            /\bCounty Judge$/u.test(name)
          );
        })
        .map(([id, rows]) => {
          const name = labels.full.get(id) ?? "";
          const isBody = rows[0]?.kind == null;
          const at = [
            /\bSheriff\b/u,
            /\b(?:District Attorney|Prosecut\w*|State's Attorney|Commonwealth's Attorney|County Attorney)\b/u,
            /\bClerk\b/u,
          ].findIndex((pattern) => pattern.test(name));
          const rank = isBody
            ? 1
            : rows[0]?.kind === "mayor" ||
                rows[0]?.kind === "head_of_government" ||
                /\b(?:Mayor|County Judge|County Executive|Executive)\b/u.test(
                  name,
                )
              ? 0
              : at < 0
                ? 6
                : at + 3;
          const parsed = rows
            .map((row) => ({ row, ...parse(row.label) }))
            .filter(
              ({ row }, _, all) =>
                row.via !== "unknown" ||
                row.label != null ||
                all.every((entry) => entry.row.label == null),
            );
          const fresh = parsed.filter(({ row }) => !row.isStale);
          const kept = parsed.filter(({ row, scheme, seat }) => {
            return (
              !row.isStale ||
              !(
                fresh.some(
                  (entry) =>
                    row.person != null && entry.row.person === row.person,
                ) ||
                parsed.some(
                  (entry) =>
                    seat != null &&
                    entry.row !== row &&
                    entry.scheme === scheme &&
                    entry.seat === seat &&
                    (entry.row.since ?? "") > (row.since ?? ""),
                ) ||
                (fresh.some(
                  (entry) => entry.seat != null && entry.scheme !== scheme,
                ) &&
                  !fresh.some((entry) => entry.scheme === scheme))
              )
            );
          });
          const schemes = new Set(
            fresh
              .map((entry) => entry.scheme)
              .filter((scheme) => scheme !== ""),
          );
          const [scheme] = schemes;
          const seats =
            rows[0]?.size ??
            (schemes.size === 1 && fresh.every((entry) => entry.seat != null)
              ? new Set(
                  (rows[0]?.labels ?? [])
                    .map((label) => parse(label))
                    .filter((entry) => entry.scheme === scheme)
                    .map((entry) => entry.seat),
                ).size
              : null);
          return [
            rank,
            {
              id,
              name,
              isBody,
              isFormer: false,
              seats: seats != null && seats > kept.length ? seats : null,
              rows: kept
                .map(({ row, scheme: own, seat, isRoman }) => ({
                  label:
                    row.label == null
                      ? null
                      : (isRoman &&
                        seat != null &&
                        kept.some(
                          (entry) =>
                            entry.scheme === own &&
                            entry.seat != null &&
                            !entry.isRoman &&
                            /\d$/u.test(entry.row.label ?? ""),
                        )
                          ? row.label.replace(/[ivxlcdm]+$/iu, seat)
                          : row.label
                        ).replaceAll(
                          /^\p{Ll}/u.test(row.label)
                            ? /(^|[\s-])(\p{Ll})/gu
                            : /^$/gu,
                          (_, before: string, letter: string) =>
                            before + letter.toUpperCase(),
                        ),
                  person: row.person,
                  name:
                    row.person == null
                      ? null
                      : (labels.full.get(row.person) ?? null),
                  party: row.party,
                  photo:
                    row.person == null ? null : (faces.get(row.person) ?? null),
                  via: row.via,
                  since: row.since,
                  contest:
                    row.contest == null
                      ? null
                      : (slugs.get(row.contest) ?? null),
                  isStale: row.isStale,
                  due:
                    row.due == null ||
                    row.isStale ||
                    row.nextDate != null ||
                    row.due < year
                      ? null
                      : row.due,
                  elected:
                    row.electedDate == null
                      ? null
                      : {
                          person: row.electedPerson,
                          name:
                            row.electedPerson == null
                              ? null
                              : (labels.full.get(row.electedPerson) ?? null),
                          date: row.electedDate,
                        },
                  next:
                    row.nextDate == null
                      ? null
                      : {
                          person: row.nextPerson,
                          name:
                            row.nextPerson == null
                              ? null
                              : (labels.full.get(row.nextPerson) ?? null),
                          date: row.nextDate,
                          contest:
                            row.nextContest == null
                              ? null
                              : (slugs.get(row.nextContest) ?? null),
                        },
                }))
                .toSorted(
                  (a, b) =>
                    (a.label ?? "").localeCompare(b.label ?? "", "en", {
                      numeric: true,
                    }) || (a.name ?? "").localeCompare(b.name ?? ""),
                ),
            },
          ] as const;
        })
        .toArray()
        .toSorted(
          ([x, a], [y, b]) =>
            x - y ||
            (a.isBody ? b.rows.length - a.rows.length : 0) ||
            a.name.localeCompare(b.name),
        );
      const formed = groups.map(([rank, group]) => {
        const isFormer =
          group.rows.every((row) => row.isStale) &&
          groups.some(([, other]) => other.rows.some((row) => !row.isStale));
        return [
          rank + (isFormer ? 10 : 0),
          { ...group, isFormer, seats: isFormer ? null : group.seats },
        ] as const;
      });
      return {
        groups: formed.toSorted(([x], [y]) => x - y).map(([, group]) => group),
        parties: parties.map(
          (id) => colors.get(id) ?? { id, label: "", short: null, color: null },
        ),
      };
    }),
  compare: os
    .meta(
      openapi({
        method: "GET",
        path: "/polities/{a}/compare/{b}",
        summary:
          "Compare two places in every contest both voted in, with the top results in each place and in the contest's own area",
      }),
    )
    .input(
      v.object({
        a: v.pipe(v.string(), v.uuid()),
        b: v.pipe(v.string(), v.uuid()),
      }),
    )
    .output(
      v.object({
        parties: v.array(PartyRef),
        places: v.array(Link),
        series: v.array(
          v.object({
            name: v.nullable(v.string()),
            isQuestion: v.boolean(),
            rank: v.number(),
            kind: v.picklist(KINDS),
            points: v.array(
              v.object({
                name: v.string(),
                path: v.nullable(v.string()),
                date: v.string(),
                year: v.number(),
                sides: v.array(
                  v.array(
                    v.object({
                      key: v.string(),
                      label: v.string(),
                      color: v.nullable(v.string()),
                      party: v.nullable(v.string()),
                      share: v.number(),
                    }),
                  ),
                ),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const [found, matched] = await Promise.all([
        db.execute<{
          id: string;
          series: string;
          owner: string | null;
          isQuestion: boolean;
          date: string;
          year: number;
          own: number | null;
          a: number;
          b: number;
        }>(sql`
          with recursive upa(id) as (
            select ${input.a}::uuid
            union
            select p.parent_id from polity_parent p join upa on p.polity_id = upa.id
          ),
          upb(id) as (
            select ${input.b}::uuid
            union
            select p.parent_id from polity_parent p join upb on p.polity_id = upb.id
          ),
          place as (
            select o.polity, a.id as area, a.k, null::date as since,
              case when r.id is not null then coalesce(r.date, '-infinity') end as until
            from (
              select distinct on (t.polity_id) t.polity_id as polity, t.area_id
              from polity_territory t
              where t.polity_id in (${input.a}::uuid, ${input.b}::uuid)
              order by t.polity_id, upper(t.valid) desc nulls first
            ) o
            join area a on a.id = o.area_id
            left join polity_relation r on r.from_polity_id = o.polity and r.kind = 'consolidated_with'
            union all
            select r.from_polity_id, a.id, a.k, coalesce(r.date, '-infinity'), null
            from polity_relation r
            cross join lateral (
              select t.area_id from polity_territory t where t.polity_id = r.to_polity_id
              order by upper(t.valid) desc nulls first limit 1
            ) t
            join area a on a.id = t.area_id
            where r.kind = 'consolidated_with' and r.from_polity_id in (${input.a}::uuid, ${input.b}::uuid)
          ),
          ups(polity, area, depth) as (
            select polity, area, 0 from place
            union
            select ups.polity, ap.parent_id, ups.depth + 1
            from ups join area_parent ap on ap.area_id = ups.area
            where ups.depth < 8
          ),
          common as (
            select area from ups group by area having count(distinct polity) = 2
          ),
          roots as materialized (
            select c.id from election e join contest c on c.election_id = e.id and not c.notional
            where e.polity_id in (select id from upa intersect select id from upb) and e.date_from is not null
              and e.status = 'held' and e.kind not in ('runoff', 'indirect')
              and c.allocation_parent_id is null and c.party_scope_id is null
              and (c.district_area_id is null or c.district_area_id in (select area from common))
          ),
          tree(id, root) as (
            select id, id from roots
            union all
            select c.id, tree.root from contest c join tree on c.allocation_parent_id = tree.id
          ),
          cand as materialized (
            select t.root, pl.polity, rs.k as set_k, pl.k as area_k
            from tree t
            cross join lateral (
              select c.district_area_id, e.date_from as day from contest c join election e on e.id = c.election_id
              where c.id = t.id offset 0
            ) c
            cross join lateral (
              select rs.k from result_set rs where rs.contest_id = t.id and rs.status = 'active' offset 0
            ) rs
            join place pl on (pl.since is null or c.day >= pl.since) and (pl.until is null or c.day < pl.until)
              and (c.district_area_id is null or c.district_area_id in (select u.area from ups u where u.polity = pl.polity))
          ),
          hits as (
            select cand.root from cand
            cross join lateral (
              select from result_rollup r
              where r.result_set_k = cand.set_k and r.area_k = cand.area_k and r.measure = any(${COUNTED})
              limit 1
            ) r
            group by cand.root
            having count(distinct cand.polity) = 2
          )
          select * from (
            select distinct on (series, e.date_from, case when c.kind = 'ballot_question' then c.id end) c.id,
              case when c.kind = 'ballot_question' then 'question'
                else concat_ws(':', coalesce(c.office_id, c.body_id), c.kind, c.round_number) end as series,
              coalesce(c.office_id, c.body_id) as owner, c.kind = 'ballot_question' as "isQuestion",
              e.date_from::text as date, ${CYCLE} as year, coalesce(own.k, home.k) as own,
              (select k from place where polity = ${input.a}::uuid
                and (since is null or e.date_from >= since) and (until is null or e.date_from < until)) as a,
              (select k from place where polity = ${input.b}::uuid
                and (since is null or e.date_from >= since) and (until is null or e.date_from < until)) as b
            from hits h
            cross join lateral (select * from contest x where x.id = h.root offset 0) c
            join election e on e.id = c.election_id
            left join area own on own.id = c.district_area_id
            left join lateral (
              select a.k from polity_territory t join area a on a.id = t.area_id
              where t.polity_id = e.polity_id
              order by t.valid @> e.date_from desc, upper(t.valid) desc nulls first
              limit 1
            ) home on true
            order by series, e.date_from, case when c.kind = 'ballot_question' then c.id end, c.term = 'remainder', c.id
          ) s
          order by date
        `),
        db.execute<{
          polity: string;
          id: string;
          election: string;
          series: string;
          owner: string;
          date: string;
          year: number;
          own: number;
          side: number;
        }>(sql`
          with recursive upa(id) as (
            select ${input.a}::uuid
            union
            select p.parent_id from polity_parent p join upa on p.polity_id = upa.id
          ),
          upb(id) as (
            select ${input.b}::uuid
            union
            select p.parent_id from polity_parent p join upb on p.polity_id = upb.id
          ),
          place as (
            select o.polity, a.id as area, a.k, null::date as since,
              case when r.id is not null then coalesce(r.date, '-infinity') end as until
            from (
              select distinct on (t.polity_id) t.polity_id as polity, t.area_id
              from polity_territory t
              where t.polity_id in (${input.a}::uuid, ${input.b}::uuid)
              order by t.polity_id, upper(t.valid) desc nulls first
            ) o
            join area a on a.id = o.area_id
            left join polity_relation r on r.from_polity_id = o.polity and r.kind = 'consolidated_with'
            union all
            select r.from_polity_id, a.id, a.k, coalesce(r.date, '-infinity'), null
            from polity_relation r
            cross join lateral (
              select t.area_id from polity_territory t where t.polity_id = r.to_polity_id
              order by upper(t.valid) desc nulls first limit 1
            ) t
            join area a on a.id = t.area_id
            where r.kind = 'consolidated_with' and r.from_polity_id in (${input.a}::uuid, ${input.b}::uuid)
          ),
          inside as (
            select polity, area, k, since, until from place
            union
            select pl.polity, ap.area_id, pl.k, pl.since, pl.until from place pl join area_parent ap on ap.parent_id = pl.area
          ),
          picked as (
            select pl.polity, c.id, e.id as election, c.term, e.date_from, 'governor' as series, o.id as owner,
              coalesce(own.k, pl.k) as own, pl.k as side, ${CYCLE} as year
            from place pl
            join office o on o.polity_id = pl.polity and o.kind = 'governor'
            join contest c on c.office_id = o.id
            join election e on e.id = c.election_id
            left join area own on own.id = c.district_area_id
            where c.allocation_parent_id is null and c.party_scope_id is null and c.round_number = 1
              and e.status = 'held' and e.kind = 'general' and e.date_from is not null
              and (pl.since is null or e.date_from >= pl.since) and (pl.until is null or e.date_from < pl.until)
            union all
            select i.polity, c.id, e.id, c.term, e.date_from, c.body_id::text, c.body_id, own.k, i.k, ${CYCLE}
            from inside i
            join contest c on c.district_area_id = i.area
            join body b on b.id = c.body_id and b.kind in ('upper_house', 'lower_house')
              and b.polity_id in (select id from upa intersect select id from upb)
            join election e on e.id = c.election_id
            join area own on own.id = c.district_area_id
            where c.allocation_parent_id is null and c.party_scope_id is null and c.round_number = 1
              and c.kind = 'body_seats' and e.status = 'held' and e.kind = 'general' and e.date_from is not null
              and (i.since is null or e.date_from >= i.since) and (i.until is null or e.date_from < i.until)
          ),
          kept as (
            select * from picked p
            where p.term is distinct from 'remainder' or not exists (
              select from picked q where q.polity = p.polity and q.series = p.series and q.date_from = p.date_from
                and q.term is distinct from 'remainder'
            )
          )
          select polity, id, election, series, owner, date_from::text as date, year, own, side
          from kept k
          where exists (
            select from kept x where x.series = k.series and x.date_from = k.date_from and x.polity <> k.polity
          )
          order by date_from, polity = ${input.b}::uuid, own
        `),
      ]);
      const taken = new Set(
        found.rows.map((row) => `${row.owner ?? ""}:${row.date}`),
      );
      const pairs = Map.groupBy(
        matched.rows.filter((row) => !taken.has(`${row.owner}:${row.date}`)),
        (row) => `${row.series}:${row.date}`,
      )
        .values()
        .map((rows) => {
          const [first] = rows;
          const sides = [input.a, input.b].map((polity) =>
            rows.filter((row) => row.polity === polity),
          );
          const isCombined = sides.some(
            (side) =>
              side.length > 1 || side.some((row) => row.own !== row.side),
          );
          return {
            id: isCombined ? (first?.election ?? "") : (first?.id ?? ""),
            series: `each:${first?.series ?? ""}`,
            owner: first?.series === "governor" ? null : (first?.owner ?? null),
            isQuestion: false,
            kind: isCombined ? ("combined" as const) : ("each" as const),
            date: first?.date ?? "",
            year: first?.year ?? 0,
            ks: [null, ...sides.map((side) => side[0]?.side ?? null)],
            rows,
          };
        })
        .toArray();
      const rows = [
        ...found.rows.map((row) => ({
          ...row,
          kind: "shared" as const,
          ks: [row.own, row.a, row.b],
          rows: null,
        })),
        ...pairs,
      ].toSorted((first, second) => first.date.localeCompare(second.date));
      const [labels, slugs, loaded, offices] = await Promise.all([
        names(
          [
            input.a,
            input.b,
            ...rows.flatMap((row) =>
              row.owner == null ? [row.id] : [row.id, row.owner],
            ),
          ],
          null,
        ),
        paths([input.a, input.b, ...rows.map((row) => row.id)]),
        Promise.all(
          rows.map(async (row) => {
            const ks = row.ks.filter((k) => k != null);
            const ids = row.rows?.map((entry) => entry.id) ?? [];
            return Promise.all([
              row.rows == null
                ? load([row.id], ks, row.date, [row.id])
                : load(
                    ids,
                    row.rows.map((entry) => entry.own),
                    row.date,
                    ids,
                    ids,
                    null,
                    "area",
                    new Map<number, Array<[number, number]>>(
                      row.rows.map((entry) => [entry.own, [[entry.side, 1]]]),
                    ),
                  ),
              branches(row.date),
            ]);
          }),
        ),
        db.execute<{ id: string; rank: number | null }>(sql`
          select id, array_position(array['head_of_state', 'head_of_government', 'governor', 'upper_house', 'body', 'mayor', 'other'], kind::text) as rank
          from office where id = any(${sql.param([...new Set(rows.flatMap((row) => row.owner ?? []))])}::uuid[])
          union all
          select id, array_position(array['head_of_state', 'head_of_government', 'governor', 'upper_house'], kind::text)
          from body where id = any(${sql.param([...new Set(rows.flatMap((row) => row.owner ?? []))])}::uuid[])
        `),
      ]);
      const ranks = new Map(offices.rows.map((row) => [row.id, row.rank]));
      const points = rows.map((row, index) => ({
        ...row,
        sides: row.ks.map((k) => {
          const here = k == null ? null : loaded[index]?.[0].get(k);
          return here == null
            ? []
            : compare(here.side, null, loaded[index]?.[1] ?? [], "party")
                .flatMap((entry) =>
                  entry.current == null
                    ? []
                    : [
                        {
                          key: entry.key,
                          label: entry.label,
                          color: entry.color,
                          party: entry.current.party,
                          share: entry.current.share,
                        },
                      ],
                )
                .toSorted((first, second) => second.share - first.share)
                .slice(0, 8);
        }),
      }));
      const parties = await refs(
        points.flatMap((point) =>
          point.sides.flatMap((side) =>
            side.flatMap((entry) => entry.party ?? []),
          ),
        ),
      );
      return {
        parties: parties.values().toArray(),
        places: [input.a, input.b].map((id) => ({
          name: labels.full.get(id) ?? "",
          path: slugs.get(id) ?? null,
        })),
        series: Map.groupBy(points, (point) => point.series)
          .values()
          .map((group) => ({
            name:
              group[0]?.owner == null
                ? null
                : (labels.short.get(group[0].owner) ??
                  labels.full.get(group[0].owner) ??
                  null),
            isQuestion: group[0]?.isQuestion ?? false,
            rank:
              group[0]?.owner == null ? 8 : (ranks.get(group[0].owner) ?? 5),
            kind: group[0]?.kind ?? "shared",
            points: group.map((point) => ({
              name: labels.full.get(point.id) ?? "",
              path: slugs.get(point.id) ?? null,
              date: point.date,
              year: point.year,
              sides: point.sides,
            })),
          }))
          .toArray()
          .toSorted(
            (first, second) =>
              KINDS.indexOf(first.kind) - KINDS.indexOf(second.kind) ||
              first.rank - second.rank ||
              second.points.length - first.points.length,
          ),
      };
    }),
};

async function portraits(ids: Array<string>) {
  const found = await db.execute<{
    person: string;
    path: string;
    license: string;
    artist: string | null;
    source: string;
    from: number | null;
    to: number | null;
  }>(sql`
    select distinct on (person_id) person_id as person, path, license, artist, source_url as source,
      extract(year from taken_from)::int as from, extract(year from taken_to)::int as to
    from person_image where person_id = any(${sql.param([...new Set(ids)])}::uuid[])
    order by person_id, recorded_from desc
  `);
  return new Map(
    found.rows.map((row) => [
      row.person,
      {
        src: `/${row.path}`,
        license: row.license,
        artist: row.artist,
        source: row.source,
        taken:
          (row.from == null && row.to == null) ||
          (row.from != null && row.to != null && row.to - row.from > 25)
            ? null
            : { from: row.from, to: row.to },
      },
    ]),
  );
}

async function branches(day: string): Promise<Array<Relation>> {
  const found = await db.execute<{
    from: string;
    to: string;
    kind: string;
  }>(sql`
    select coalesce(fm.into_id, a.party_id) as from, coalesce(tm.into_id, a.parent_party_id) as to, a.kind::text
    from party_affiliation a
    left join entity_merge fm on fm.entity_id = a.party_id
    left join entity_merge tm on tm.entity_id = a.parent_party_id
    where a.kind = 'regional_branch' and a.valid @> ${day}::date
  `);
  return found.rows.map((row) => ({ ...row, toLabel: "" }));
}

export async function refs(ids: Array<string>, on: string | null = null) {
  const unique = [...new Set(ids)];
  const [labels, colors] = await Promise.all([
    names(unique, on),
    db.execute<{ party: string; color: string }>(sql`
      select distinct on (party) party, color from (
        select party_id as party, color, false as lent, upper(valid) as until from party_color
        where party_id = any(${sql.param(unique)}::uuid[])
        union all
        select coalesce(fm.into_id, r.from_party_id), pc.color, true, e.date_from
        from party_relation r
        left join entity_merge fm on fm.entity_id = r.from_party_id
        left join entity_merge tm on tm.entity_id = r.to_party_id
        left join election e on e.id = r.election_id
        join party_color pc on pc.party_id = coalesce(tm.into_id, r.to_party_id) and (e.id is null or pc.valid @> e.date_from)
        where r.kind = 'list_vehicle_for' and coalesce(fm.into_id, r.from_party_id) = any(${sql.param(unique)}::uuid[])
        union all
        select a.party_id, pc.color, true, upper(pc.valid)
        from party_affiliation a
        left join entity_merge bm on bm.entity_id = a.parent_party_id
        join party_color pc on pc.party_id = coalesce(bm.into_id, a.parent_party_id)
        where a.kind = 'regional_branch' and a.party_id = any(${sql.param(unique)}::uuid[])
      ) x
      order by party, lent, until desc nulls first
    `),
  ]);
  const palette = new Map(colors.rows.map((row) => [row.party, row.color]));
  return new Map(
    unique.map((id) => [
      id,
      {
        id,
        label: labels.full.get(id) ?? "",
        short: labels.short.get(id) ?? null,
        color: palette.get(id) ?? null,
      },
    ]),
  );
}

async function namesakes(
  type: "person" | "party",
  base: string,
  polity: string | null,
) {
  const words = base.split("-").filter((word) => word !== "");
  if (words.length === 0) {
    return [];
  }
  const found = await db.execute<{ id: string }>(sql`
    select entity_id as id from search_document
    where type = ${type}::entity_type
      and tsv @@ to_tsquery('simple', ${words.map((word) => `${word}:*`).join(" & ")})
      and (${polity}::uuid is null or polity_id = ${polity}::uuid)
    order by rank_boost desc, entity_id
  `);
  const ids = found.rows.map((row) => row.id);
  if (type === "party") {
    const labels = await names(ids);
    return ids.filter((id) => segment(labels.full.get(id) ?? "") === base);
  }
  const known = await db.execute<{ entity_id: string; text: string }>(sql`
    select entity_id, text from name
    where entity_id = any(${sql.param(ids)}::uuid[]) and kind in ('official', 'english')
  `);
  const matched = new Set(
    known.rows
      .filter((row) => segment(row.text) === base)
      .map((row) => row.entity_id),
  );
  return ids.filter((id) => matched.has(id));
}

async function locate(
  type: "person" | "party",
  slug: string,
  polity: string | null,
) {
  const exact = await namesakes(type, slug, polity);
  if (exact[0] != null) {
    return exact[0];
  }
  const suffix = /^(.+)-(\d+)$/u.exec(slug);
  if (suffix?.[1] == null) {
    return null;
  }
  const peers = await namesakes(type, suffix[1], polity);
  return peers[Number(suffix[2]) - 1] ?? (peers.length === 1 ? peers[0] : null);
}

async function chambers(polity: string) {
  const found = await db.execute<{ id: string; kind: string }>(sql`
    select b.id, b.kind::text as kind from body b
    where b.polity_id = ${polity}::uuid and b.kind <> 'electoral_college'
    order by b.kind, b.id
  `);
  const labels = await names(found.rows.map((row) => row.id));
  const taken = new Map<string, number>();
  return found.rows.map((row) => {
    const name = labels.full.get(row.id) ?? "";
    const base = segment(name);
    const seen = (taken.get(base) ?? 0) + 1;
    taken.set(base, seen);
    return {
      id: row.id,
      kind: row.kind,
      name,
      slug: seen > 1 ? `${base}-${seen}` : base,
    };
  });
}

export const bodies = {
  locate: os
    .meta(
      openapi({
        method: "GET",
        path: "/bodies/locate",
        summary:
          "Find the chamber a /chambers/{polity}/{slug} path names, the polity given by its path",
      }),
    )
    .input(v.object({ polity: v.string(), slug: v.string() }))
    .output(v.object({ id: v.string(), polity: v.string() }))
    .handler(async ({ input }) => {
      const owner = await db.execute<{ id: string }>(sql`
        select s.entity_id as id from slug s join polity p on p.id = s.entity_id
        where s.path = ${input.polity} and s.is_current
      `);
      const polity = owner.rows[0]?.id;
      const peers = polity == null ? [] : await chambers(polity);
      const found = peers.find((row) => row.slug === input.slug);
      if (polity == null || found == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return { id: found.id, polity };
    }),
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/bodies/{id}",
        summary:
          "Get a chamber: its size over time, the seats each party held after every election, and the governments that sat",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        name: v.string(),
        kind: v.string(),
        polity: v.object({
          id: v.string(),
          name: v.string(),
          path: v.nullable(v.string()),
        }),
        crumbs: v.array(
          v.object({ name: v.string(), path: v.nullable(v.string()) }),
        ),
        siblings: v.array(
          v.object({ id: v.string(), name: v.string(), path: v.string() }),
        ),
        sizes: v.array(
          v.object({
            seats: v.number(),
            from: v.nullable(v.string()),
            to: v.nullable(v.string()),
          }),
        ),
        tenures: v.number(),
        closed: v.nullable(v.string()),
        makeup: v.array(
          v.object({
            start: v.string(),
            apportioned: v.nullable(v.number()),
            seceded: v.number(),
            parties: v.array(
              v.object({
                party: v.nullable(v.string()),
                caucus: v.nullable(v.string()),
                category: v.picklist(["elected", "appointed", "ex_officio"]),
                seats: v.number(),
                isIndependent: v.boolean(),
              }),
            ),
          }),
        ),
        partners: v.array(v.array(v.string())),
        parties: v.array(
          v.object({
            id: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
            lean: v.nullable(v.number()),
          }),
        ),
        next: v.nullable(
          v.object({
            date: v.string(),
            name: v.string(),
            path: v.nullable(v.string()),
            anchor: v.string(),
            seats: v.number(),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        kind: string;
        polity: string;
        tenures: number;
        closed: string | null;
      }>(sql`
        select b.kind::text as kind, b.polity_id as polity,
          (select count(*)::int from tenure t where t.body_id = b.id) as tenures,
          coalesce(
            (select max(upper(z.valid)) from body_size z where z.body_id = b.id having bool_and(not upper_inf(z.valid))),
            (select min(p.valid_from) from polity_period p where p.polity_id = b.polity_id and p.valid_from > s.last),
            (select min(e.date_from) from body y join contest c on c.body_id = y.id join election e on e.id = c.election_id
              where b.kind in ('lower_house', 'upper_house') and y.polity_id = b.polity_id and y.id <> b.id
                and y.kind in ('lower_house', 'upper_house', 'unicameral') and e.date_from > s.last
                and s.last < current_date - interval '20 years'
                and not exists (
                  select from contest k join election w on w.id = k.election_id
                  where k.body_id = y.id and w.date_from between s.first and s.last
                ))
          )::text as closed
        from body b
        cross join lateral (
          select min(e.date_from) as first, max(e.date_from) as last from contest c join election e on e.id = c.election_id
          where c.body_id = b.id and e.date_from <= current_date
        ) s
        where b.id = ${input.id}::uuid
      `);
      const body = found.rows[0];
      if (body == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const [labels, slugs, crumbs, peers, sizes, makeup, upcoming] =
        await Promise.all([
          names([input.id, body.polity]),
          paths([body.polity]),
          breadcrumbs(body.polity),
          chambers(body.polity),
          db.execute<{ seats: number; from: string | null; to: string | null }>(
            sql`
            select seats, lower(valid)::text as from, upper(valid)::text as to
            from body_size where body_id = ${input.id}::uuid
            order by lower(valid) nulls first
          `,
          ),
          db.execute<{
            start: string;
            party: string | null;
            caucus: string | null;
            category: "elected" | "appointed" | "ex_officio";
            seats: number;
            isIndependent: boolean;
          }>(sql`
          with recursive chain as materialized (
            select t.id, t.acquired_via::text as via,
              coalesce(t.succeeds_tenure_id, case when t.seat_id is not null then
                lag(t.id) over (partition by t.seat_id order by t.from_date nulls first) end) as before
            from tenure t where t.body_id = ${input.id}::uuid
          ),
          kinds(id, category) as (
            select c.id, case when c.via in ('ex_officio', 'indirect', 'appointed') then c.via else 'elected' end
            from chain c
            where c.before is null or c.via not in ('appointed', 'acting', 'succession', 'list_replacement', 'co_opted', 'trustee')
            union all
            select c.id, k.category from kinds k join chain c on c.before = k.id
            where c.via in ('appointed', 'acting', 'succession', 'list_replacement', 'co_opted', 'trustee')
          ),
          t as materialized (
            select t.id, t.seat_id, t.from_date, coalesce(t.to_date, 'infinity'::date) as to_date, t.contest_id, t.person_id,
              (select e.date_from from contest c join election e on e.id = c.election_id where c.id = t.contest_id) as won
            from tenure t left join seat st on st.id = t.seat_id
            where t.body_id = ${input.id}::uuid and t.from_date is not null and coalesce(st.voting, true)
          ),
          starts as (
            select t.from_date as start from t
            group by t.from_date
            having count(*) >= 0.15 * coalesce(
              (select z.seats from body_size z where z.body_id = ${input.id}::uuid and z.valid @> t.from_date limit 1),
              (select max(n) from (select count(*) as n from t group by from_date) m)
            )
          ),
          sat as (
            select r.start, r.id from (
              select q.start, q.id,
                row_number() over (partition by q.start, q.slot order by q.from_date > q.start, q.won desc nulls last, q.from_date, q.to_date) as rank,
                greatest(1, count(*) filter (where q.won = q.first) over (partition by q.start, q.slot)) as cap
              from (
                select s.start, t.id, coalesce(t.seat_id, t.id) as slot, t.from_date, t.to_date, t.won,
                  min(t.won) filter (where t.won <= s.start) over (partition by s.start, coalesce(t.seat_id, t.id)) as first
                from starts s join t on t.to_date > s.start
                  and (t.from_date <= s.start or t.seat_id is not null and t.from_date < s.start + 30)
                where not exists (select from starts l where l.start > s.start and l.start < s.start + 90)
              ) q
            ) r where r.rank <= r.cap
          ),
          fallback as materialized (
            select distinct on (t.id) t.id, coalesce(pm.into_id, o.party_id, po.party_id) as party,
              cd.is_independent as independent
            from t
            join contest_option o on o.contest_id = t.contest_id
              or o.contest_id in (select k.id from contest k where k.allocation_parent_id = t.contest_id)
            join candidacy cd on cd.option_id = o.id and cd.person_id = t.person_id
            left join contest_option po on po.id = o.parent_option_id
            left join entity_merge pm on pm.entity_id = coalesce(o.party_id, po.party_id)
            where not exists (select from tenure_affiliation a where a.tenure_id = t.id and a.kind = 'party')
            order by t.id
          )
          select x.start::text as start, coalesce(bm.into_id, br.parent_party_id, x.party) as party,
            nullif(x.caucus, coalesce(bm.into_id, br.parent_party_id, x.party)) as caucus, x.category, count(*)::int as seats,
            bool_and(x.party is null and coalesce(x.independent, false)) as "isIndependent"
          from (
            select s.start, case when k.category in ('appointed', 'ex_officio') then k.category else 'elected' end as category,
              case when k.category in ('appointed', 'ex_officio') then false when a.found then a.party_id is null else f.independent end as independent,
              case when k.category in ('appointed', 'ex_officio') then null when a.found then a.party else f.party end as party,
              (select coalesce(pm.into_id, g.party_id) from tenure_affiliation g
                left join entity_merge pm on pm.entity_id = g.party_id
                where g.tenure_id = s.id and g.kind = 'parliamentary_group' and g.party_id is not null
                  and g.valid @> s.start and k.category is distinct from 'appointed' and k.category is distinct from 'ex_officio' limit 1) as caucus
            from sat s left join fallback f on f.id = s.id
            left join kinds k on k.id = s.id
            left join lateral (
              select a.party_id, coalesce(pm.into_id, a.party_id) as party, true as found from tenure_affiliation a
              left join entity_merge pm on pm.entity_id = a.party_id
              where a.tenure_id = s.id and a.kind = 'party'
              order by coalesce(a.valid @> s.start, false) desc, a.party_id is null, lower(a.valid) nulls first limit 1
            ) a on true
          ) x
          left join party_affiliation br on br.party_id = x.party
            and br.kind = 'regional_branch' and br.valid @> x.start
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          group by 1, 2, 3, 4 order by 1, x.category = 'elected' desc, 5 desc
        `),
          db.execute<{ id: string; date: string; seats: number }>(sql`
          select e.id, e.date_from::text as date, sum(coalesce(c.magnitude, 1))::int as seats
          from contest c join election e on e.id = c.election_id
          where c.body_id = ${input.id}::uuid and c.allocation_parent_id is null
            and c.party_scope_id is null and not c.notional and e.date_from >= current_date
          group by e.id, e.date_from
          order by e.date_from, seats desc
          limit 1
        `),
        ]);
      const home = slugs.get(body.polity) ?? null;
      const held = [
        ...new Set(
          makeup.rows.flatMap((row) =>
            [row.party, row.caucus].filter((party) => party != null),
          ),
        ),
      ];
      const starts = [...new Set(makeup.rows.map((row) => row.start))];
      const apportioned = await db.execute<{
        start: string;
        seats: number;
        seceded: number;
      }>(sql`
        select s.start::text as start, sum(a.seats)::int as seats,
          coalesce(sum(greatest(0, a.seats - (
            select count(distinct t.seat_id) from tenure t join seat st on st.id = t.seat_id
            where t.body_id = a.body_id and st.voting and st.apportioned_area_id = a.area_id
              and t.from_date <= s.start and coalesce(t.to_date, 'infinity'::date) > s.start
          ))) filter (where exists (
            select from representation_gap g where g.body_id = a.body_id and g.area_id = a.area_id and g.valid @> s.start
          )), 0)::int as seceded
        from unnest(${sql.param(starts)}::date[]) s(start)
        join body_apportionment a on a.body_id = ${input.id}::uuid and a.valid @> s.start
        group by s.start
      `);
      const next = upcoming.rows[0];
      const [nextNames, nextPaths] =
        next == null
          ? [null, null]
          : await Promise.all([names([next.id]), paths([next.id])]);
      const entitled = new Map(apportioned.rows.map((row) => [row.start, row]));
      const last = await db.execute<{ id: string; date: string }>(sql`
        select c.id, e.date_from::text as date from contest c join election e on e.id = c.election_id
        where c.body_id = ${input.id}::uuid and c.allocation_parent_id is null
          and c.party_scope_id is null and e.kind = 'general' and not c.notional
          and e.date_from = (
            select max(e2.date_from) from contest c2 join election e2 on e2.id = c2.election_id
            where c2.body_id = ${input.id}::uuid and e2.kind = 'general'
          )
      `);
      const [colors, positions, pairs] = await Promise.all([
        refs(held),
        db.execute<{ party_id: string; lean: number }>(sql`
          select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
          where party_id = any(${sql.param(held)}::uuid[])
          order by party_id, valid_from desc nulls last
        `),
        last.rows.length === 0
          ? null
          : db.execute<{ from: string; to: string }>(
              sisters(
                last.rows.map((row) => row.id),
                last.rows[0]?.date ?? "infinity",
              ),
            ),
      ]);
      const lean = new Map(
        positions.rows.map((row) => [row.party_id, row.lean]),
      );
      return {
        name: labels.full.get(input.id) ?? "",
        kind: body.kind,
        polity: {
          id: body.polity,
          name: labels.full.get(body.polity) ?? "",
          path: home,
        },
        crumbs,
        siblings:
          home == null
            ? []
            : peers.map((peer) => ({
                id: peer.id,
                name: peer.name,
                path: `chambers/${home}/${peer.slug}`,
              })),
        sizes: sizes.rows,
        tenures: body.tenures,
        closed: body.closed,
        makeup: Map.groupBy(makeup.rows, (row) => row.start)
          .entries()
          .map(([start, rows]) => ({
            start,
            apportioned: entitled.get(start)?.seats ?? null,
            seceded: entitled.get(start)?.seceded ?? 0,
            parties: rows.map((row) => ({
              party: row.party,
              caucus: row.caucus,
              category: row.category,
              seats: row.seats,
              isIndependent: row.isIndependent,
            })),
          }))
          .toArray(),
        parties: held.map((id) => ({
          ...(colors.get(id) ?? { id, label: "", short: null, color: null }),
          lean: lean.get(id) ?? null,
        })),
        partners:
          pairs?.rows.map((row) => [row.from.slice(6), row.to.slice(6)]) ?? [],
        next:
          next == null
            ? null
            : {
                date: next.date,
                name: nextNames?.full.get(next.id) ?? "",
                path: nextPaths?.get(next.id) ?? null,
                anchor: `race-${segment(labels.full.get(input.id) ?? "")}`,
                seats: next.seats,
              },
      };
    }),
  members: os
    .meta(
      openapi({
        method: "GET",
        path: "/bodies/{id}/members",
        summary:
          "List who sat in a chamber during the term that began at an election, with each member's party, seat, and tenure; where the archive holds no tenures, the members the election returned",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        from: v.pipe(v.string(), v.isoDate()),
        until: v.optional(v.pipe(v.string(), v.isoDate())),
      }),
    )
    .output(
      v.object({
        start: v.nullable(v.string()),
        end: v.nullable(v.string()),
        isSeated: v.boolean(),
        members: v.array(
          v.object({
            person: v.string(),
            name: v.string(),
            party: v.nullable(v.string()),
            caucus: v.nullable(v.string()),
            isIndependent: v.boolean(),
            left: v.nullable(v.string()),
            place: v.nullable(v.string()),
            path: v.nullable(v.string()),
            seat: v.nullable(v.string()),
            region: v.nullable(v.string()),
            from: v.nullable(v.string()),
            to: v.nullable(v.string()),
            acquired: v.nullable(v.string()),
            ended: v.nullable(v.string()),
            isVoting: v.boolean(),
          }),
        ),
        apportionment: v.array(
          v.object({
            area: v.string(),
            seats: v.number(),
            from: v.string(),
            to: v.nullable(v.string()),
            gaps: v.array(
              v.object({ from: v.string(), to: v.nullable(v.string()) }),
            ),
          }),
        ),
        parties: v.array(
          v.object({
            id: v.string(),
            label: v.string(),
            short: v.nullable(v.string()),
            color: v.nullable(v.string()),
            lean: v.nullable(v.number()),
            parent: v.nullable(v.string()),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const until = input.until ?? null;
      const window = await db.execute<{
        start: string | null;
        end: string | null;
      }>(sql`
        select
          (select t.from_date::text from tenure t
            where t.body_id = ${input.id}::uuid and t.from_date >= ${input.from}::date
              and (${until}::date is null or t.from_date < ${until}::date)
            group by t.from_date order by count(*) desc, t.from_date limit 1) as start,
          (select t.from_date::text from tenure t
            where t.body_id = ${input.id}::uuid and ${until}::date is not null
              and t.from_date >= ${until}::date and t.from_date < ${until}::date + 400
            group by t.from_date order by count(*) desc, t.from_date limit 1) as end
      `);
      const start = window.rows[0]?.start ?? null;
      const end = window.rows[0]?.end ?? until;
      const seated =
        start == null
          ? null
          : await db.execute<{
              person: string;
              party: string | null;
              caucus: string | null;
              isIndependent: boolean;
              left: string | null;
              place: string | null;
              contest: string | null;
              seat: string | null;
              region: string | null;
              from: string | null;
              to: string | null;
              acquired: string;
              ended: string | null;
              isVoting: boolean;
            }>(sql`
              select t.person_id as person, t.from_date::text as from, t.to_date::text as to,
                coalesce(s.voting, true) as "isVoting",
                case when t.acquired_via = 'elected' and t.contest_id is null and t.succeeds_tenure_id is not null
                  then 'by_election' else t.acquired_via::text end as acquired,
                t.ended_via::text as ended, t.contest_id as contest,
                s.label as seat, s.apportioned_area_id as region, coalesce(c.district_area_id, pc.district_area_id, s.district_area_id, inherited.place) as place,
                coalesce(a.found and a.party_id is null, false) as "isIndependent",
                (select coalesce(pm.into_id, g.party_id) from tenure_affiliation g
                  left join entity_merge pm on pm.entity_id = g.party_id
                  where g.tenure_id = t.id and g.kind = 'parliamentary_group' and g.party_id is not null
                    and g.valid @> greatest(t.from_date, ${start}::date) limit 1) as caucus,
                case when a.found then a.party else
                  (select coalesce(pm.into_id, o.party_id, po.party_id) from contest_option o
                    join candidacy cd on cd.option_id = o.id and cd.person_id = t.person_id
                    left join contest_option po on po.id = o.parent_option_id
                    left join entity_merge pm on pm.entity_id = coalesce(o.party_id, po.party_id)
                    where o.contest_id = t.contest_id or o.contest_id in (
                      select k.id from contest k where k.allocation_parent_id = t.contest_id)
                    limit 1)
                end as party,
                (select coalesce(pm.into_id, a.party_id) from tenure_affiliation a
                  left join entity_merge pm on pm.entity_id = a.party_id
                  where a.tenure_id = t.id and a.kind = 'party' and a.party_id is not null
                  order by lower(a.valid) desc nulls last limit 1) as left
              from tenure t
              left join seat s on s.id = t.seat_id
              left join contest c on c.id = t.contest_id
              left join contest pc on pc.id = c.allocation_parent_id
              left join lateral (
                with recursive up(succeeds, contest) as (
                  select p.succeeds_tenure_id, p.contest_id from tenure p
                  where p.id = t.succeeds_tenure_id and t.contest_id is null
                  union all
                  select p.succeeds_tenure_id, p.contest_id from tenure p join up on p.id = up.succeeds
                  where up.contest is null
                )
                select k.district_area_id as place from up join contest k on k.id = up.contest limit 1
              ) inherited on true
              left join lateral (
                select a.party_id, coalesce(pm.into_id, a.party_id) as party, true as found from tenure_affiliation a
                left join entity_merge pm on pm.entity_id = a.party_id
                where a.tenure_id = t.id and a.kind = 'party'
                order by coalesce(a.valid @> greatest(t.from_date, ${start}::date), false) desc, a.party_id is null,
                  lower(a.valid) nulls first limit 1
              ) a on true
              where t.body_id = ${input.id}::uuid
                and coalesce(t.from_date, '-infinity'::date) < coalesce(${end}::date, 'infinity'::date)
                and coalesce(t.to_date, 'infinity'::date) > ${start}::date
              order by t.from_date, t.person_id
            `);
      const returned =
        seated == null || seated.rows.length === 0
          ? await db.execute<{
              person: string;
              party: string | null;
              isIndependent: boolean;
              place: string | null;
              contest: string;
            }>(sql`
              with picked as (
                select c.id, c.district_area_id, c.allocation_parent_id from contest c
                join election e on e.id = c.election_id
                where c.body_id = ${input.id}::uuid and e.date_from >= ${input.from}::date
                  and e.date_from < coalesce(${until}::date, 'infinity'::date)
                  and e.kind in ('general', 'local')
              )
              select distinct on (cd.person_id) cd.person_id as person, c.id as contest,
                coalesce(pm.into_id, o.party_id, po.party_id) as party, coalesce(cd.is_independent, false) as "isIndependent",
                coalesce(c.district_area_id, pc.district_area_id) as place
              from picked c
              left join contest pc on pc.id = c.allocation_parent_id
              join contest_option o on o.contest_id = c.id
              left join contest_option po on po.id = o.parent_option_id
              join outcome x on x.option_id = o.id and x.kind = 'elected'
              join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
              join candidacy cd on cd.option_id = o.id
                and (cd.id = x.candidacy_id or (x.candidacy_id is null and cd.role = 'head'))
              left join entity_merge pm on pm.entity_id = coalesce(o.party_id, po.party_id)
              order by cd.person_id, c.allocation_parent_id is null desc
            `)
          : null;
      const rows =
        returned == null
          ? (seated?.rows ?? [])
          : returned.rows.map((row) => ({
              ...row,
              caucus: null,
              left: null,
              seat: null,
              region: null,
              from: null,
              to: null,
              acquired: null,
              ended: null,
              isVoting: true,
            }));
      const parties = [
        ...new Set(
          rows
            .flatMap((row) => [row.party, row.left, row.caucus])
            .filter((id) => id != null),
        ),
      ];
      const [labels, slugs, colors, positions, branches, apportionment] =
        await Promise.all([
          names([
            ...new Set([
              ...rows.map((row) => row.person),
              ...rows.flatMap((row) => row.place ?? []),
            ]),
          ]),
          paths([...new Set(rows.flatMap((row) => row.contest ?? []))]),
          refs(parties),
          db.execute<{ party_id: string; lean: number }>(sql`
            select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
            where party_id = any(${sql.param(parties)}::uuid[])
            order by party_id, valid_from desc nulls last
          `),
          db.execute<{ party: string; parent: string }>(sql`
            select distinct on (a.party_id) a.party_id as party, coalesce(pm.into_id, a.parent_party_id) as parent
            from party_affiliation a left join entity_merge pm on pm.entity_id = a.parent_party_id
            where a.party_id = any(${sql.param(parties)}::uuid[]) and a.kind = 'regional_branch'
              and (a.valid @> ${start ?? input.from}::date or upper(a.valid) is null)
            order by a.party_id, lower(a.valid) desc nulls last
          `),
          db.execute<{
            area: string;
            seats: number;
            from: string;
            to: string | null;
            gaps: Array<{ from: string; to: string | null }>;
          }>(sql`
            select a.area_id as area, a.seats, a.valid_from::text as from, a.valid_to::text as to,
              coalesce(json_agg(json_build_object('from', g.valid_from::text, 'to', g.valid_to::text)
                order by g.valid_from) filter (where g.valid_from is not null), '[]') as gaps
            from body_apportionment a
            left join representation_gap g on g.body_id = a.body_id and g.area_id = a.area_id
              and g.valid && daterange(${start ?? input.from}::date, ${end}::date)
            where a.body_id = ${input.id}::uuid and ${returned == null}
              and a.valid && daterange(${start ?? input.from}::date, ${end}::date)
            group by a.area_id, a.seats, a.valid_from, a.valid_to
          `),
        ]);
      const lean = new Map(
        positions.rows.map((row) => [row.party_id, row.lean]),
      );
      const parent = new Map(
        branches.rows.map((row) => [row.party, row.parent]),
      );
      return {
        start,
        end,
        isSeated: returned == null,
        members: rows.map((row) => ({
          person: row.person,
          name: labels.full.get(row.person) ?? "",
          party: row.party,
          caucus: row.caucus === row.party ? null : row.caucus,
          isIndependent: row.isIndependent,
          left: row.left === row.party ? null : row.left,
          place:
            row.place == null ? null : (labels.full.get(row.place) ?? null),
          path: row.contest == null ? null : (slugs.get(row.contest) ?? null),
          seat: row.seat,
          region: row.region,
          from: row.from,
          to: row.to,
          acquired: row.acquired,
          ended: row.ended,
          isVoting: row.isVoting,
        })),
        apportionment: apportionment.rows,
        parties: parties.map((id) => ({
          ...(colors.get(id) ?? { id, label: "", short: null, color: null }),
          lean: lean.get(id) ?? null,
          parent: parent.get(id) ?? null,
        })),
      };
    }),
};

export const entities = {
  path: os
    .meta(
      openapi({
        method: "GET",
        path: "/entities/{id}/path",
        summary:
          "Resolve any entity's ID to its canonical page path: a polity's, election's, or contest's slug, /people/{slug} for a person, /parties/{polity}/{slug} for a party, and /chambers/{polity}/{slug} for a chamber",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(v.object({ path: v.nullable(v.string()) }))
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        type: string;
        slug: string | null;
        polity: string | null;
        retired: string | null;
      }>(sql`
        select e.id, e.type::text as type,
          (select s.path from slug s where s.entity_id = e.id and s.is_current) as slug,
          (select s.path from party p join slug s on s.entity_id = p.polity_id and s.is_current where p.id = e.id) as polity,
          (select r.reason from entity_retirement r where r.entity_id = e.id) as retired
        from entity e
        where e.id = coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
      `);
      const entity = found.rows[0];
      if (entity == null) {
        throw new ORPCError("NOT_FOUND");
      }
      if (entity.retired != null) {
        throw new ORPCError("GONE", { message: entity.retired });
      }
      if (entity.slug != null) {
        return { path: entity.slug };
      }
      if (entity.type === "body") {
        const owner = await db.execute<{
          polity: string;
          path: string | null;
        }>(sql`
          select b.polity_id as polity,
            (select s.path from slug s where s.entity_id = b.polity_id and s.is_current) as path
          from body b where b.id = ${entity.id}::uuid
        `);
        const home = owner.rows[0];
        const peers = home?.path == null ? [] : await chambers(home.polity);
        const found = peers.find((row) => row.id === entity.id);
        return {
          path:
            found == null || home?.path == null
              ? null
              : `chambers/${home.path}/${found.slug}`,
        };
      }
      if (entity.type !== "person" && entity.type !== "party") {
        return { path: null };
      }
      const labels = await names([entity.id]);
      const known =
        entity.type === "person"
          ? await db.execute<{ text: string }>(sql`
            select text from name where entity_id = ${entity.id}::uuid and kind in ('official', 'english')
            order by kind = 'english' and lang = 'en' and script = 'Latn' desc, is_preferred desc, text
          `)
          : null;
      const owner =
        entity.type === "party"
          ? await db.execute<{ polity: string | null }>(sql`
            select polity_id as polity from party where id = ${entity.id}::uuid
          `)
          : null;
      const bases = new Set([
        ...(known?.rows.map((row) => segment(row.text)) ?? []),
        segment(labels.full.get(entity.id) ?? ""),
      ]);
      let slug: string | null = null;
      for (const base of bases) {
        const peers = await namesakes(
          entity.type,
          base,
          owner?.rows[0]?.polity ?? null,
        );
        const at = peers.indexOf(entity.id);
        if (at >= 0) {
          slug = at > 0 ? `${base}-${at + 1}` : base;
          break;
        }
      }
      if (slug == null) {
        return { path: null };
      }
      return {
        path:
          entity.type === "person"
            ? `people/${slug}`
            : ["parties", entity.polity, slug]
                .filter((part) => part != null)
                .join("/"),
      };
    }),
};

export const people = {
  locate: os
    .meta(
      openapi({
        method: "GET",
        path: "/people/locate",
        summary: "Find the person a /people/{slug} path names",
      }),
    )
    .input(v.object({ slug: v.string() }))
    .output(v.object({ id: v.string() }))
    .handler(async ({ input }) => {
      const id = await locate("person", input.slug, null);
      if (id == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return { id };
    }),
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/people/{id}",
        summary: "Get a person and their election record",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        name: v.string(),
        born: v.nullable(v.string()),
        precision: v.nullable(v.string()),
        died: v.nullable(v.string()),
        diedPrecision: v.nullable(v.string()),
        isGone: v.boolean(),
        wikidata: v.nullable(v.string()),
        photo: Photo,
        party: v.nullable(PartyRef),
        caucus: v.nullable(PartyRef),
        isIndependent: v.boolean(),
        served: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            isBody: v.boolean(),
            path: v.nullable(v.string()),
            from: v.nullable(v.string()),
            to: v.nullable(v.string()),
            spans: v.array(
              v.object({
                from: v.nullable(v.string()),
                to: v.nullable(v.string()),
              }),
            ),
            seats: v.array(
              v.object({
                name: v.string(),
                from: v.nullable(v.string()),
                to: v.nullable(v.string()),
                ended: v.nullable(v.string()),
              }),
            ),
          }),
        ),
        elected: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            isBody: v.boolean(),
            path: v.nullable(v.string()),
            from: v.string(),
            to: v.nullable(v.string()),
          }),
        ),
        faithless: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.string(),
            year: v.number(),
            votes: v.number(),
            isFaithless: v.boolean(),
            states: v.array(v.string()),
          }),
        ),
        scattered: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.string(),
            year: v.number(),
            votes: v.nullable(v.number()),
            total: v.nullable(v.number()),
          }),
        ),
        record: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.string(),
            year: v.number(),
            kind: v.string(),
            election: v.string(),
            measure: v.string(),
            votes: v.nullable(v.number()),
            total: v.nullable(v.number()),
            majority: v.optional(v.nullable(v.number())),
            isUnopposed: v.optional(v.boolean()),
            isWriteIn: v.optional(v.boolean()),
            outcome: v.nullable(v.string()),
            note: v.optional(v.nullable(v.string())),
            electoral: v.nullable(
              v.object({ votes: v.number(), total: v.number() }),
            ),
            party: v.nullable(PartyRef),
            nominated: v.optional(
              v.array(v.object({ party: v.string(), label: v.string() })),
            ),
            isIndependent: v.optional(v.boolean()),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        born: string | null;
        precision: string | null;
        died: string | null;
        diedPrecision: string | null;
        isGone: boolean;
        wikidata: string | null;
      }>(sql`
        select p.id, p.birth_date::text as born, p.birth_precision::text as precision,
          p.death_date::text as died, p.death_precision::text as "diedPrecision",
          coalesce(p.death_date is not null or p.birth_date < current_date - interval '110 years', false) as "isGone",
          (select x.value from external_id x where x.entity_id = p.id and x.scheme = 'wikidata' limit 1) as wikidata
        from person p
        where p.id = coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
      `);
      const person = found.rows[0];
      if (person == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const rows = await db.execute<{
        root: string;
        date: string;
        year: number;
        kind: string;
        election: string;
        measure: number | null;
        votes: number | null;
        total: number | null;
        majority: number | null;
        unopposed: boolean;
        written: boolean | null;
        office: string | null;
        outcome: string | null;
        note: string | null;
        party: string | null;
        independent: boolean;
        scoped: boolean;
        party_label: string | null;
        party_short: string | null;
        party_color: string | null;
      }>(sql`
        with recursive ids(id) as (
          select ${person.id}::uuid
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        ),
        mine as materialized (
          select o.id as option, o.contest_id, cd.id as candidacy, cd.role,
            coalesce(pm.into_id, lp.party) as party, cd.is_independent as independent,
            (select party_scope_id is not null from contest where id = o.contest_id) as scoped,
            o.kind = 'write_in' as written
          from candidacy cd join contest_option o on o.id = cd.option_id
          cross join lateral (
            select coalesce(cd.member_of_party_id, o.party_id, ${fused(sql`o.id`, sql`o.contest_id`)},
              (select party_scope_id from contest where id = o.contest_id)) as party
          ) lp
          left join entity_merge pm on pm.entity_id = lp.party
          where cd.person_id in (select id from ids) and cd.role in ('head', 'list_member')
            and not ${stray(sql`cd.id`, sql`o.id`, sql`o.contest_id`)}
        ),
        lines as materialized (
          select o.k from contest_option o
          where o.id in (select option from mine) or o.parent_option_id in (select option from mine)
        ),
        up(id, parent) as (
          select c.id, case when c.kind = 'list_allocation' then null else c.allocation_parent_id end
          from contest c where c.id in (select contest_id from mine)
          union
          select c.id, c.allocation_parent_id from up join contest c on c.id = up.parent
        ),
        grp(root, id) as (
          select id, id from up where parent is null
          union all
          select grp.root, c.id from contest c join grp on c.allocation_parent_id = grp.id
        ),
        placed as materialized (
          select c.id as root, a.k as area, e.date_from as date, ${CYCLE} as year, e.kind::text as kind, e.id as election
          from contest c join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          where c.id in (select id from up where parent is null)
        ),
        picked as (
          select distinct on (rs.contest_id) grp.root, rs.k, p.area
          from grp cross join lateral (select * from result_set x where x.contest_id = grp.id and x.status <> 'withdrawn' offset 0) rs join placed p on p.root = grp.root
          join lateral (
            select 1 from result_rollup r where r.result_set_k = rs.k and r.area_k = p.area and r.measure = any(${COUNTED}) limit 1
          ) hit on true
          order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
        ),
        fetched as (
          select p.root, p.k as rk, r.* from picked p cross join lateral (
            select r.option_k as option, r.measure, r.value::float8 as value, r.count_round,
              min(r.count_round) over (partition by r.measure) as first,
              max(r.count_round) over (partition by r.measure) as last
            from result_rollup r
            where r.result_set_k = p.k and r.area_k = p.area
              and (r.measure = any(${COUNTED}) and r.option_k <> 0 or r.measure = 2 and r.option_k = 0)
          ) r
        ),
        rounds as (
          select f.root, f.rk, f.measure,
            case when bool_or(f.count_round = f.last and f.value > 0 and f.option in (select k from lines))
              and (select coalesce(c.magnitude, 1) <= 1 from contest c where c.id = f.root) then max(f.last) else min(f.first) end as round
          from fetched f group by f.root, f.rk, f.measure
        ),
        mains as (
          select root, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main,
            sum(value) filter (where measure = 2 and count_round = first) as appointed
          from fetched group by root
        ),
        totals as (
          select f.root, m.main, m.appointed, sum(f.value) as total,
            sum(f.value) filter (where f.option in (select k from lines)) as votes
          from fetched f join mains m on m.root = f.root and m.main = f.measure
            join rounds d on d.root = f.root and d.rk = f.rk and d.measure = f.measure
          where f.count_round = d.round and f.option <> 0
          group by f.root, m.main, m.appointed
        ),
        margins as (
          select o.root,
            case when bool_or(o.elected) and bool_or(o.named and not o.elected)
              then min(o.value) filter (where o.elected) - max(o.value) filter (where o.named and not o.elected) end as majority
          from (
            select f.root, top.k as option, top.kind, coalesce(
              sum(f.value) filter (where co.parent_option_id is null), sum(f.value)
            ) as value, top.kind in ('candidate', 'ticket') as named, exists (
              select from outcome x join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
              where x.option_id = top.id and x.candidacy_id is null and x.kind in ('elected', 'won')
            ) as elected, f.first <> f.last as "isCounted"
            from fetched f join mains m on m.root = f.root and m.main = f.measure
              join contest_option co on co.k = f.option
              join contest_option top on top.id = coalesce(co.parent_option_id, co.id)
            where f.count_round = f.last and f.option <> 0 and (
              f.first = f.last or (select coalesce(c.magnitude, 1) <= 1 from contest c where c.id = f.root)
            )
            group by f.root, top.k, top.id, top.kind, f.first, f.last
          ) o
          where not exists (select from contest c where c.allocation_parent_id = o.root)
          group by o.root
          having (bool_or(o."isCounted") or count(distinct o.option) filter (where o.kind <> 'write_in') = (
            select count(*) from contest_option co where co.contest_id = o.root and co.parent_option_id is null and co.kind <> 'write_in'
          ))
            and not exists (
              select from contest_option co where co.contest_id = o.root and co.kind not in ('candidate', 'ticket', 'write_in', 'others', 'independents')
            )
        )
        select p.root, p.date::text as date, p.year, p.kind, p.election, t.main::int as measure, t.votes,
          (select bool_and(m.written) from mine m join grp g on g.id = m.contest_id and g.root = p.root) as written,
          (select office_id from contest where id = p.root) as office,
          g.majority, exists (
            select from result_set rs where rs.contest_id = p.root and ${latest("rs")} and rs.kind = 'tacit'
          ) as unopposed,
          case when t.main = 11 then coalesce(t.appointed, t.total) else t.total end as total,
          coalesce(
            (select (array_agg(x.kind::text order by array_position(array['declined', 'elected', 'advanced', 'won', 'tied']::outcome_kind[], x.kind)))[1]
              from mine m join outcome x on x.option_id = m.option
                and (x.candidacy_id = m.candidacy or (x.candidacy_id is null and m.role = 'head'))
              join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
              where m.contest_id = p.root),
            (select 'elsewhere' from mine m join contest c on c.id = m.contest_id join election e on e.id = c.election_id
              join outcome x on x.option_id = m.option and x.kind in ('elected', 'won')
                and (x.candidacy_id = m.candidacy or (x.candidacy_id is null and m.role = 'head'))
              join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
              where coalesce(e.parent_id, e.id) = (select coalesce(pe.parent_id, pe.id) from election pe where pe.id = p.election)
                and m.contest_id not in (select g.id from grp g where g.root = p.root)
                and exists (select from mine l join grp g on g.id = l.contest_id and g.root = p.root where l.role = 'list_member')
              limit 1),
            (select 'lost' from contest_option o join outcome x on x.option_id = o.id and x.kind in ('elected', 'won', 'advanced')
              join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
              where o.contest_id = p.root limit 1)
          ) as outcome,
          (select (array_agg(x.notes order by array_position(array['declined', 'elected', 'advanced', 'won', 'tied']::outcome_kind[], x.kind)) filter (where x.notes is not null))[1]
            from mine m join outcome x on x.option_id = m.option
              and (x.candidacy_id = m.candidacy or (x.candidacy_id is null and m.role = 'head'))
            join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
            where m.contest_id = p.root) as note,
          lead.party, coalesce(lead.independent, false) as independent, coalesce(lead.scoped, false) as scoped,
          (select n.text from name n where n.entity_id = lead.party and n.kind in ('official', 'english')
            order by coalesce(n.valid @> p.date, false) desc, n.is_preferred desc, (n.lang = 'en') desc,
              (n.kind = 'official') desc, n.recorded_from desc, n.id limit 1) as party_label,
          ${abbreviate(sql`lead.party`, sql`p.date`)} as party_short,
          coalesce(${paint(sql`lead.party`, sql`select election_id from contest where id = p.root`, sql`p.date`)},
            (select pc.color from party_color pc where pc.party_id = lead.party
              order by upper(pc.valid) desc nulls first limit 1)) as party_color
        from placed p left join totals t on t.root = p.root
        left join margins g on g.root = p.root
        left join lateral (
          select m.party, m.independent, m.scoped from mine m join grp g on g.id = m.contest_id and g.root = p.root
          order by m.party is null, m.independent limit 1
        ) lead on true
        order by p.date desc
      `);
      const roots = rows.rows.map((row) => row.root);
      const peer = (row: (typeof rows.rows)[number]) =>
        row.kind === "general" && row.office != null
          ? rows.rows.find(
              (other) =>
                other.kind === "indirect" &&
                other.year === row.year &&
                other.office === row.office,
            )
          : undefined;
      const isStray = (row: (typeof rows.rows)[number]) =>
        row.written === true &&
        !["elected", "won", "advanced", "tied", "declined"].includes(
          row.outcome ?? "",
        ) &&
        (row.votes == null ||
          row.total == null ||
          row.total === 0 ||
          row.votes / row.total < 0.05);
      const kept = rows.rows.filter((row) => !isStray(row));
      const lone = kept.filter(
        (row) =>
          row.kind === "indirect" &&
          row.measure === 11 &&
          kept.every((other) => peer(other) !== row),
      );
      const faithless =
        lone.length === 0
          ? []
          : (
              await db.execute<{
                root: string;
                state: string;
                votes: number;
                isFaithless: boolean;
              }>(sql`
                with recursive ids(id) as (
                  select ${person.id}::uuid
                  union
                  select em.entity_id from entity_merge em join ids on em.into_id = ids.id
                )
                select c.id as root, a.id as state, r.value::float8 as votes,
                  coalesce(w.person not in (select id from ids) and not w.died, false) as "isFaithless"
                from contest c
                join election e on e.id = c.election_id
                join contest_option o on o.contest_id = c.id
                join result_set rs on rs.contest_id = c.id and ${latest("rs")}
                cross join lateral (
                  select x.area_k, x.value from result_rollup x
                  where x.result_set_k = rs.k and x.option_k = o.k and x.measure = 11 and x.value > 0 offset 0
                ) r
                join area a on a.k = r.area_k and a.scheme_code <> 'us.nation'
                left join lateral (
                  select coalesce(em.into_id, hc.person_id) as person,
                    coalesce((select p.death_date < e.date_from from person p where p.id = hc.person_id), false) as died
                  from contest g
                  join election ge on ge.id = g.election_id
                  join contest gk on gk.id = g.id or gk.allocation_parent_id = g.id
                  join result_set gs on gs.contest_id = gk.id and gs.is_default
                  cross join lateral (
                    select y.option_k, y.value from result_rollup y
                    where y.result_set_k = gs.k and y.area_k = r.area_k and y.measure = 1 and y.option_k <> 0 offset 0
                  ) gv
                  join contest_option go on go.k = gv.option_k
                  left join candidacy hc on hc.option_id = coalesce(go.parent_option_id, go.id) and hc.role = 'head'
                  left join entity_merge em on em.entity_id = hc.person_id
                  where g.office_id = c.office_id and g.party_scope_id is null and g.allocation_parent_id is null
                    and ge.kind = 'general' and extract(year from ge.date_from) = extract(year from e.date_from)
                  order by gv.value desc limit 1
                ) w on true
                where c.id = any(${sql.param(lone.map((row) => row.root))}::uuid[])
                  and exists (
                    select from candidacy cd where cd.option_id = o.id and cd.role = 'head' and cd.person_id in (select id from ids)
                  )
                  and exists (
                    select from contest g join election ge on ge.id = g.election_id
                    where g.office_id = c.office_id and g.party_scope_id is null and ge.kind = 'general'
                      and extract(year from ge.date_from) = extract(year from e.date_from)
                  )
                order by r.value desc, a.k
              `)
            ).rows;
      const unfaithful = Map.groupBy(faithless, (row) => row.root);
      const tenures = await db.execute<{
        owner: string;
        isBody: boolean;
        from: string | null;
        to: string | null;
        place: string | null;
        via: string | null;
      }>(sql`
        with recursive ids(id) as (
          select ${person.id}::uuid
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        )
        select coalesce(t.body_id, t.office_id) as owner, t.body_id is not null as "isBody",
          t.from_date::text as from, t.to_date::text as to,
          case when t.ended_via not in ('term_end', 'dissolved') then t.ended_via::text end as via,
          coalesce((select k.district_area_id from contest k where k.id = t.contest_id), t.contest_id) as place
        from tenure t where t.person_id in (select id from ids)
        order by coalesce(t.body_id, t.office_id), t.from_date nulls first
      `);
      const joins = await db.execute<{
        id: string;
        date: string;
        year: number;
        place: string | null;
      }>(sql`
        with recursive ids(id) as (
          select ${person.id}::uuid
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        )
        select t.id, t.from_date::text as date, extract(year from t.from_date)::int as year, inherited.place
        from tenure t
        left join lateral (
          with recursive up(succeeds, contest) as (
            select p.succeeds_tenure_id, p.contest_id from tenure p where p.id = t.succeeds_tenure_id
            union all
            select p.succeeds_tenure_id, p.contest_id from tenure p join up on p.id = up.succeeds
            where up.contest is null
          )
          select k.district_area_id as place from up join contest k on k.id = up.contest limit 1
        ) inherited on true
        where t.person_id in (select id from ids) and t.acquired_via = 'elected' and t.contest_id is null
          and t.succeeds_tenure_id is not null and t.from_date is not null
          and not exists (
            select from candidacy cy join contest_option o on o.id = cy.option_id
            join contest c on c.id = o.contest_id join election e on e.id = c.election_id
            where cy.person_id in (select id from ids) and c.office_id = t.office_id
              and e.date_from between t.from_date - 400 and t.from_date
          )
      `);
      const spells = merge(tenures.rows, 365);
      const won = new Set(
        rows.rows
          .filter((row) => {
            const indirect = peer(row);
            const outcome = indirect == null ? row.outcome : indirect.outcome;
            return outcome === "elected" || outcome === "won";
          })
          .map((row) => row.root),
      );
      const seated = new Set(spells.map((spell) => spell.owner));
      const wins = await db.execute<{
        owner: string;
        isBody: boolean;
        from: string;
        to: string | null;
      }>(sql`
        select o.owner, o.owner is not distinct from c.body_id as "isBody", e.date_from::text as from,
          coalesce(
            (select min(e2.date_from) from contest c2 join election e2 on e2.id = c2.election_id
              where c2.body_id is not distinct from c.body_id and c2.office_id is not distinct from c.office_id
                and c2.district_area_id is not distinct from c.district_area_id and c2.party_scope_id is null
                and e2.kind in ('general', 'local') and e2.date_from > e.date_from + 60),
            e.date_from + (e.date_from - (
              select max(e2.date_from) from contest c2 join election e2 on e2.id = c2.election_id
              where c2.body_id is not distinct from c.body_id and c2.office_id is not distinct from c.office_id
                and c2.district_area_id is not distinct from c.district_area_id and c2.party_scope_id is null
                and e2.kind in ('general', 'local') and e2.date_from < e.date_from - 60
                and e2.date_from > e.date_from - 3650
            )),
            (select min(e2.date_from) from contest c2 join election e2 on e2.id = c2.election_id
              where c.body_id is not null and c2.body_id = c.body_id and c2.office_id is not distinct from c.office_id
                and c2.party_scope_id is null and e2.kind = 'general' and e2.date_from > e.date_from + 60)
          )::text as to
        from contest c join election e on e.id = c.election_id
        cross join lateral (
          select case when c.kind = 'office' then coalesce(c.office_id, c.body_id) else coalesce(c.body_id, c.office_id) end as owner
        ) o
        where c.id = any(${sql.param([...won])}::uuid[]) and c.party_scope_id is null
          and (c.body_id is not null or c.office_id is not null)
          and e.kind in ('general', 'by_election', 'special', 'runoff', 'local')
        order by 1, e.date_from
      `);
      const terms = merge(
        wins.rows.filter((row) => !seated.has(row.owner)),
        120,
      );
      for (const term of terms) {
        for (const other of [...terms, ...spells]) {
          if (
            other.owner !== term.owner &&
            other.from != null &&
            other.from > term.from &&
            (term.to == null || other.from < term.to)
          ) {
            term.to = other.from;
          }
        }
      }
      const joint = await db.execute<{
        root: string;
        party: string;
        isOwn: boolean;
      }>(sql`
        with recursive ids(id) as (
          select ${person.id}::uuid
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        )
        select distinct coalesce(case when c.kind = 'list_allocation' then null else c.allocation_parent_id end, c.id) as root,
          coalesce(pm.into_id, n.party_id) as party, n.party_id = o.party_id as "isOwn"
        from candidacy cy
        join candidacy_nomination n on n.candidacy_id = cy.id and n.kind = 'nominated'
        join contest_option o on o.id = cy.option_id
        join contest c on c.id = o.contest_id
        left join entity_merge pm on pm.entity_id = n.party_id
        where cy.person_id in (select id from ids) and cy.role = 'head'
          and coalesce(case when c.kind = 'list_allocation' then null else c.allocation_parent_id end, c.id) = any(${sql.param(roots)}::uuid[])
      `);
      const nominations = Map.groupBy(joint.rows, (row) => row.root);
      const [labels, slugs] = await Promise.all([
        names(
          [
            person.id,
            ...faithless.map((row) => row.state),
            ...joint.rows.map((row) => row.party),
            ...roots,
            ...new Set([...spells, ...terms].map((spell) => spell.owner)),
            ...tenures.rows.flatMap((row) =>
              row.place == null ? [] : [row.place],
            ),
            ...joins.rows.flatMap((row) =>
              row.place == null ? [] : [row.place],
            ),
          ],
          null,
          [person.id],
        ),
        paths(roots),
      ]);
      const record = [
        ...kept
          .filter(
            (row) =>
              !unfaithful.has(row.root) &&
              (row.kind !== "indirect" ||
                kept.every((other) => peer(other) !== row)),
          )
          .map((row) => {
            const found = (nominations.get(row.root) ?? [])
              .map((entry) => ({
                party: entry.party,
                isOwn: entry.isOwn,
                label:
                  labels.short.get(entry.party) ??
                  labels.full.get(entry.party) ??
                  "",
              }))
              .filter((entry) => entry.label !== "");
            const indirect = peer(row);
            return {
              name: labels.full.get(row.root) ?? "",
              path: slugs.get(row.root) ?? null,
              date: row.date,
              year: row.year,
              kind: row.kind,
              election: row.election,
              measure: MEASURES.get(row.measure ?? 1) ?? "votes",
              votes: row.votes,
              total: row.total,
              majority:
                row.outcome === "elected" || row.outcome === "won"
                  ? row.majority
                  : null,
              isUnopposed: row.unopposed,
              isWriteIn: row.written === true,
              outcome: indirect == null ? row.outcome : indirect.outcome,
              note: indirect == null ? row.note : null,
              isIndependent: row.party == null && row.independent,
              electoral:
                indirect?.votes != null && indirect.total != null
                  ? { votes: indirect.votes, total: indirect.total }
                  : row.kind === "indirect" &&
                      row.votes != null &&
                      row.total != null
                    ? { votes: row.votes, total: row.total }
                    : null,
              party:
                row.party == null
                  ? null
                  : {
                      id: row.party,
                      label: row.party_label ?? "",
                      short: row.party_short,
                      color: row.party_color,
                    },
              nominated:
                found.length > 1 || found[0]?.isOwn === false
                  ? found
                      .toSorted(
                        (a, b) =>
                          Number(b.isOwn) - Number(a.isOwn) ||
                          a.label.localeCompare(b.label),
                      )
                      .map(({ party, label }) => ({ party, label }))
                  : [],
            };
          }),
        ...joins.rows.map((row) => ({
          name: row.place == null ? "" : (labels.full.get(row.place) ?? ""),
          path: null,
          date: row.date,
          year: row.year,
          kind: "seated",
          election: row.id,
          measure: "votes",
          votes: null,
          total: null,
          outcome: "elected",
          electoral: null,
          party: null,
        })),
      ].toSorted((a, b) => b.date.localeCompare(a.date));
      const held = await db.execute<{
        party: string | null;
        start: string | null;
        stop: string | null;
      }>(sql`
        with recursive ids(id) as (
          select ${person.id}::uuid
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        )
        select coalesce(pm.into_id, x.party) as party, x.start::text as start, x.stop::text as stop from (
          select m.party_id as party, m.valid_from as start, m.valid_to as stop, 0 as rank
          from person_party_membership m left join party k on k.id = m.party_id
          where m.person_id in (select id from ids) and k.kind is distinct from 'caucus'
          union all
          select a.party_id, coalesce(lower(a.valid), t.from_date), coalesce(upper(a.valid), t.to_date), 1
          from tenure t join tenure_affiliation a on a.tenure_id = t.id and a.kind = 'party'
          where t.person_id in (select id from ids)
        ) x
        left join entity_merge pm on pm.entity_id = x.party
        order by x.stop desc nulls first, x.rank, x.start desc nulls last
        limit 1
      `);
      const general = kept.find(
        (row) => !row.scoped && (row.party != null || row.independent),
      );
      const current = held.rows.find(
        (row) =>
          row.stop == null ||
          general == null ||
          (row.stop > general.date &&
            !(
              general.independent &&
              (row.start == null || row.start <= general.date)
            )),
      );
      const isIndependent =
        current == null ? general?.independent === true : current.party == null;
      const own = isIndependent
        ? null
        : (current?.party ??
          general?.party ??
          rows.rows.find((row) => row.party != null)?.party ??
          null);
      const sits = await db.execute<{ party: string; stop: string | null }>(sql`
        with recursive ids(id) as (
          select ${person.id}::uuid
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        )
        select coalesce(pm.into_id, a.party_id) as party, coalesce(upper(a.valid), t.to_date)::text as stop
        from tenure t join tenure_affiliation a on a.tenure_id = t.id and a.kind = 'parliamentary_group'
        left join entity_merge pm on pm.entity_id = a.party_id
        where t.person_id in (select id from ids) and a.party_id is not null
        order by coalesce(upper(a.valid), t.to_date) desc nulls first limit 1
      `);
      const sat = sits.rows[0];
      const caucus =
        sat != null && sat.stop === current?.stop && sat.party !== own
          ? sat.party
          : null;
      const housed = await db.execute<{ id: string; polity: string }>(sql`
        select id, polity_id::text as polity from body
        where id = any(${sql.param([...spells, ...terms].map((spell) => spell.owner))}::uuid[])
        union all
        select id, polity_id::text from office
        where id = any(${sql.param([...spells, ...terms].map((spell) => spell.owner))}::uuid[])
      `);
      const homes = [...new Set(housed.rows.map((row) => row.polity))];
      const [faces, owned, peers, homeSlugs] = await Promise.all([
        portraits([person.id]),
        refs([own, caucus].filter((id) => id != null)),
        Promise.all(homes.map((polity) => chambers(polity))).then(
          (found) =>
            new Map(found.flat().map((peer) => [peer.id, peer.slug] as const)),
        ),
        paths(homes),
      ]);
      const chamberPath = new Map(
        housed.rows.flatMap((row) => {
          const home = homeSlugs.get(row.polity);
          const slug = peers.get(row.id);
          return home == null
            ? []
            : [
                [
                  row.id,
                  slug == null ? home : `chambers/${home}/${slug}`,
                ] as const,
              ];
        }),
      );
      const now = new Date();
      const stamp = now.toISOString().slice(4, 10);
      const earliest =
        [
          ...record.map((entry) => entry.date),
          ...spells.flatMap((spell) =>
            spell.from == null ? [] : [spell.from],
          ),
          ...terms.map((term) => term.from),
        ].toSorted((a, b) => a.localeCompare(b))[0] ?? "9999";
      return {
        name: labels.full.get(person.id) ?? "",
        born: person.born,
        precision: person.precision,
        died: person.died,
        diedPrecision: person.diedPrecision,
        isGone:
          person.isGone || earliest < `${now.getUTCFullYear() - 92}${stamp}`,
        wikidata: person.wikidata,
        photo: faces.get(person.id) ?? null,
        party: own == null ? null : (owned.get(own) ?? null),
        caucus: caucus == null ? null : (owned.get(caucus) ?? null),
        isIndependent,
        served: [...Map.groupBy(spells, (spell) => spell.owner).entries()]
          .map(([owner, group]) => {
            const seats: Array<{
              name: string;
              from: string | null;
              to: string | null;
              ended: string | null;
            }> = [];
            for (const row of tenures.rows) {
              if (
                row.place == null ||
                !group.some(
                  (spell) =>
                    spell.isBody &&
                    row.owner === spell.owner &&
                    (spell.from == null || (row.from ?? "") >= spell.from) &&
                    (spell.to == null || (row.to ?? spell.to) <= spell.to),
                )
              ) {
                continue;
              }
              const last = seats.at(-1);
              const name = (labels.full.get(row.place) ?? "").replace(
                /^.+? in /u,
                "",
              );
              if (
                last?.name === name &&
                (last.ended == null ||
                  (last.ended === "appointed" &&
                    last.to != null &&
                    row.from != null &&
                    Date.parse(row.from) - Date.parse(last.to) <=
                      120 * 86_400_000))
              ) {
                last.to = row.to;
                last.ended = row.via;
              } else {
                seats.push({
                  name,
                  from: row.from,
                  to: row.to,
                  ended: row.via,
                });
              }
            }
            return {
              id: owner,
              name: labels.full.get(owner) ?? "",
              isBody: group[0]?.isBody ?? false,
              path: chamberPath.get(owner) ?? null,
              from: group[0]?.from ?? null,
              to: group.some((spell) => spell.to == null)
                ? null
                : (group.at(-1)?.to ?? null),
              spans: group.map((spell) => ({ from: spell.from, to: spell.to })),
              seats,
            };
          })
          .toSorted(
            (a, b) =>
              Number(b.to == null) - Number(a.to == null) ||
              (b.to ?? "").localeCompare(a.to ?? "") ||
              (b.from ?? "").localeCompare(a.from ?? ""),
          ),
        elected: terms
          .map((term) => ({
            id: term.owner,
            name: labels.full.get(term.owner) ?? "",
            isBody: term.isBody,
            path: chamberPath.get(term.owner) ?? null,
            from: term.from,
            to: term.to,
          }))
          .toSorted((a, b) => b.from.localeCompare(a.from)),
        record,
        faithless: lone.flatMap((row) =>
          Map.groupBy(
            unfaithful.get(row.root) ?? [],
            (entry) => entry.isFaithless,
          )
            .entries()
            .map(([isFaithless, entries]) => {
              let votes = 0;
              for (const entry of entries) {
                votes += entry.votes;
              }
              return {
                name: labels.full.get(row.root) ?? "",
                path: slugs.get(row.root) ?? null,
                date: row.date,
                year: row.year,
                votes,
                isFaithless,
                states: entries.map(
                  (entry) => labels.full.get(entry.state) ?? "",
                ),
              };
            })
            .toArray()
            .toSorted((a, b) => Number(b.isFaithless) - Number(a.isFaithless)),
        ),
        scattered: rows.rows
          .filter((row) => isStray(row) && (row.votes ?? 0) > 0)
          .map((row) => ({
            name: labels.full.get(row.root) ?? "",
            path: slugs.get(row.root) ?? null,
            date: row.date,
            year: row.year,
            votes: row.votes,
            total: row.total,
          })),
      };
    }),
  parties: os
    .meta(
      openapi({
        method: "GET",
        path: "/people/{id}/parties",
        summary:
          "List a person's parties over time: memberships, the party they sat for in each office, and every party that nominated, endorsed, or carried them on a ballot line",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.array(
        v.object({
          party: v.nullable(PartyRef),
          spans: v.array(
            v.object({
              kind: v.picklist(["member", "office", "group"]),
              from: v.nullable(v.string()),
              to: v.nullable(v.string()),
            }),
          ),
          ballots: v.array(
            v.object({
              date: v.string(),
              how: v.picklist([
                "nominated",
                "line",
                "endorsed",
                "supported",
                "member",
                "independent",
              ]),
              name: v.string(),
              path: v.nullable(v.string()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const [spans, ballots] = await Promise.all([
        db.execute<{
          party: string | null;
          kind: "member" | "office" | "group";
          from: string | null;
          to: string | null;
        }>(sql`
          with recursive ids(id) as (
            select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
            union
            select em.entity_id from entity_merge em join ids on em.into_id = ids.id
          )
          select coalesce(bm.into_id, br.parent_party_id, pm.into_id, x.party) as party, x.kind, x.from, x.to from (
            select m.party_id as party, case when k.kind = 'caucus' then 'group' else 'member' end as kind,
              m.valid_from::text as from, m.valid_to::text as to
            from person_party_membership m left join party k on k.id = m.party_id
            where m.person_id in (select id from ids)
            union all
            select a.party_id, case when a.kind = 'party' then 'office' else 'group' end,
              coalesce(lower(a.valid), t.from_date)::text, coalesce(upper(a.valid), t.to_date)::text
            from tenure t join tenure_affiliation a on a.tenure_id = t.id
            where t.person_id in (select id from ids)
          ) x
          left join entity_merge pm on pm.entity_id = x.party
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, x.party) and br.kind = 'regional_branch'
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          order by 1, 2, x.from nulls first
        `),
        db.execute<{
          party: string | null;
          how:
            | "nominated"
            | "line"
            | "endorsed"
            | "supported"
            | "member"
            | "independent";
          date: string;
          contest: string;
        }>(sql`
          with recursive ids(id) as (
            select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
            union
            select em.entity_id from entity_merge em join ids on em.into_id = ids.id
          ),
          mine as materialized (
            select cd.id, cd.member_of_party_id as member, cd.is_independent as independent, o.id as option,
              coalesce(o.party_id, ${fused(sql`o.id`, sql`o.contest_id`)}) as line,
              coalesce(case when c.kind = 'list_allocation' then null else c.allocation_parent_id end, c.id) as contest,
              e.date_from
            from candidacy cd join contest_option o on o.id = cd.option_id
            join contest c on c.id = o.contest_id join election e on e.id = c.election_id
            where cd.person_id in (select id from ids) and cd.role in ('head', 'running_mate', 'list_member')
              and o.kind <> 'write_in' and not ${stray(sql`cd.id`, sql`o.id`, sql`o.contest_id`)}
          ),
          claims as (
            select m.contest, m.date_from, n.party_id as party, n.kind::text as how
            from mine m join candidacy_nomination n on n.candidacy_id = m.id
            union all
            select m.contest, m.date_from, m.line, case when m.independent then 'independent' else 'line' end
            from mine m where m.line is not null or m.independent
            union all
            select m.contest, m.date_from, ch.party_id, 'line'
            from mine m join contest_option ch on ch.parent_option_id = m.option and ch.party_id is not null
            union all
            select m.contest, m.date_from, m.member, 'member'
            from mine m where m.member is not null
          )
          select distinct on (coalesce(bm.into_id, br.parent_party_id, pm.into_id, x.party), x.contest)
            coalesce(bm.into_id, br.parent_party_id, pm.into_id, x.party) as party, x.how, x.date_from::text as date, x.contest
          from claims x left join entity_merge pm on pm.entity_id = x.party
          left join party_affiliation br on br.party_id = coalesce(pm.into_id, x.party) and br.kind = 'regional_branch'
          left join entity_merge bm on bm.entity_id = br.parent_party_id
          order by coalesce(bm.into_id, br.parent_party_id, pm.into_id, x.party), x.contest,
            array_position(array['nominated', 'line', 'member', 'independent', 'endorsed', 'supported'], x.how)
        `),
      ]);
      const parties = [
        ...new Set(
          [...spans.rows, ...ballots.rows].flatMap((row) => row.party ?? []),
        ),
      ];
      const contests = [...new Set(ballots.rows.map((row) => row.contest))];
      const [entities, labels, slugs] = await Promise.all([
        refs(parties),
        names(contests),
        paths(contests),
      ]);
      const lanes = new Map<
        string,
        {
          party: {
            id: string;
            label: string;
            short: string | null;
            color: string | null;
          } | null;
          spans: Array<{
            kind: "member" | "office" | "group";
            from: string | null;
            to: string | null;
          }>;
          ballots: Array<{
            date: string;
            how:
              | "nominated"
              | "line"
              | "endorsed"
              | "supported"
              | "member"
              | "independent";
            name: string;
            path: string | null;
          }>;
        }
      >();
      const lane = (party: string | null) => {
        const key = party ?? "";
        const found = lanes.get(key) ?? {
          party: party == null ? null : (entities.get(party) ?? null),
          spans: [],
          ballots: [],
        };
        lanes.set(key, found);
        return found;
      };
      for (const row of spans.rows) {
        const into = lane(row.party).spans;
        const last = into.at(-1);
        if (
          last?.kind === row.kind &&
          last.to != null &&
          row.from != null &&
          Date.parse(row.from) - Date.parse(last.to) < 365 * 864e5
        ) {
          last.to = row.to;
        } else {
          into.push({ kind: row.kind, from: row.from, to: row.to });
        }
      }
      for (const row of ballots.rows) {
        lane(row.party).ballots.push({
          date: row.date,
          how: row.how,
          name: labels.full.get(row.contest) ?? "",
          path: slugs.get(row.contest) ?? null,
        });
      }
      return lanes
        .values()
        .map((entry) => ({
          entry: {
            ...entry,
            ballots: entry.ballots
              .map((ballot) =>
                ballot.how === "nominated" &&
                entry.ballots.some((other) => other.how === "line")
                  ? { ...ballot, how: "line" as const }
                  : ballot,
              )
              .toSorted((a, b) => a.date.localeCompare(b.date)),
          },
          start:
            [
              ...entry.spans.map((span) => span.from ?? ""),
              ...entry.ballots.map((ballot) => ballot.date),
            ].toSorted((a, b) => a.localeCompare(b))[0] ?? "",
        }))
        .toArray()
        .toSorted((a, b) => a.start.localeCompare(b.start))
        .map(({ entry }) => entry);
    }),
};

async function renames(id: string) {
  const [rows, founded] = await Promise.all([
    db.execute<{
      text: string;
      kind: string;
      lang: string;
      from: string | null;
      to: string | null;
      isPreferred: boolean;
    }>(sql`
      select text, kind::text, lang, lower(valid)::text as from, upper(valid)::text as to, is_preferred as "isPreferred"
      from name where entity_id = ${id}::uuid and kind in ('official', 'abbreviation')
    `),
    db.execute<{ date: string | null }>(sql`
      select min(date)::text as date from party_relation
      where (to_party_id = ${id}::uuid and kind in ('merged_into', 'refounded_as'))
        or (from_party_id = ${id}::uuid and kind in ('split_from', 'successor_of'))
    `),
  ]);
  const official = rows.rows.filter((row) => row.kind === "official");
  const current = official
    .filter((row) => row.to == null)
    .toSorted(
      (a, b) =>
        (b.from ?? "").localeCompare(a.from ?? "") ||
        Number(b.isPreferred) - Number(a.isPreferred),
    )[0];
  if (current == null) {
    return [];
  }
  const shorts = rows.rows.filter((row) => row.kind === "abbreviation");
  const names = Map.groupBy(
    official.filter((row) => row.lang === current.lang),
    (row) => row.text,
  )
    .entries()
    .flatMap(([text, group]) => {
      const froms = group.flatMap((row) => row.from ?? []).toSorted();
      const tos = group.flatMap((row) => row.to ?? []).toSorted();
      const isCurrent = text === current.text;
      return isCurrent || tos.length > 0
        ? [
            {
              name: text,
              from: froms[0] ?? null,
              to: isCurrent ? null : (tos.at(-1) ?? null),
            },
          ]
        : [];
    })
    .toArray()
    .toSorted(
      (a, b) =>
        (a.to ?? "9999").localeCompare(b.to ?? "9999") ||
        (a.from ?? "").localeCompare(b.from ?? ""),
    );
  return names.map((entry, index) => {
    const from =
      entry.from ??
      (index === 0
        ? founded.rows[0]?.date != null &&
          (entry.to == null || founded.rows[0].date < entry.to)
          ? founded.rows[0].date
          : null
        : (names[index - 1]?.to ?? null));
    const matches = shorts.filter(
      (row) =>
        (row.from != null || row.to != null) &&
        row.to === entry.to &&
        row.text !== entry.name,
    );
    const short =
      matches.find((row) => row.from === from && row.lang === current.lang) ??
      matches.find((row) => row.lang === current.lang) ??
      matches[0];
    return { ...entry, from, short: short?.text ?? null };
  });
}

export const parties = {
  elections: os
    .meta(
      openapi({
        method: "GET",
        path: "/parties/{id}/elections",
        summary:
          "List the elections a party ran in, newest first, each with how many races it entered and the seats or offices it won",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.object({
        count: v.number(),
        elections: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            year: v.nullable(v.number()),
            races: v.number(),
            won: v.number(),
            seats: v.number(),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        election: string;
        date: string | null;
        year: number | null;
        races: number;
        won: number;
        seats: number;
        total: number;
      }>(sql`
        with recursive ids(id) as (
          select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        ),
        branches as (
          select pa.party_id as id from party_affiliation pa
          where pa.kind = 'regional_branch' and pa.parent_party_id in (select id from ids)
        ),
        family as (
          select id from ids union select id from branches
          union select em.entity_id from entity_merge em where em.into_id in (select id from branches)
        ),
        below(id) as (
          select ${input.within ?? null}::uuid where ${input.within ?? null}::uuid is not null
          union
          select pp.polity_id from polity_parent pp join below on pp.parent_id = below.id
        ),
        mine as materialized (
          select o.id as option, o.contest_id, c.allocation_parent_id as parent, c.election_id
          from contest_option o join contest c on c.id = o.contest_id
          where not c.notional and o.party_id in (select id from family) and (
            ${input.within ?? null}::uuid is null or exists (
              select from contest c join election e on e.id = c.election_id
              where c.id = o.contest_id and e.polity_id in (select id from below)
            )
          )
        ),
        up(id, parent, election_id) as (
          select contest_id, parent, election_id from mine
          union
          select c.id, c.allocation_parent_id, c.election_id from up
          cross join lateral (select * from contest c where c.id = up.parent offset 0) c
        ),
        won as (
          select w.contest_id, coalesce(bool_or(w.kind in ('body_seats', 'list_allocation')), false) as chamber, sum(w.seats)::int as seats
          from (
            select m.contest_id, m.option, case when c.party_scope_id is null then c.kind::text end as kind, max(coalesce(x.seats, 1)) as seats
            from mine m join contest c on c.id = m.contest_id
            join outcome x on x.option_id = m.option and x.candidacy_id is null and x.kind in ('elected', 'won')
            join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
            group by m.contest_id, m.option, c.kind, c.party_scope_id
          ) w
          group by w.contest_id
        ),
        tops as (
          select up.id, case when ${input.within ?? null}::uuid is null then coalesce(e.parent_id, e.id) else e.id end as election,
            e.date_from, ${CYCLE} as year
          from up join election e on e.id = up.election_id
          where case when ${input.within ?? null}::uuid is null then up.parent is null
            else e.polity_id in (select id from below) and not exists (
              select from contest p join election pe on pe.id = p.election_id
              where p.id = up.parent and pe.polity_id in (select id from below)
            ) end
        )
        select t.election, min(t.date_from)::text as date, min(t.year) as year,
          count(distinct t.id)::int as races,
          count(distinct w.contest_id) filter (where not w.chamber)::int as won,
          coalesce(sum(w.seats) filter (where w.chamber), 0)::int as seats,
          count(*) over ()::int as total
        from tops t
        left join won w on w.contest_id = t.id
        group by t.election
        order by min(t.date_from) desc nulls last, count(distinct t.id) desc
        limit 200
      `);
      const ids = found.rows.map((row) => row.election);
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      return {
        count: found.rows[0]?.total ?? 0,
        elections: found.rows.map((row) => ({
          name: labels.full.get(row.election) ?? "",
          path: slugs.get(row.election) ?? null,
          date: row.date,
          year: row.year,
          races: row.races,
          won: row.won,
          seats: row.seats,
        })),
      };
    }),
  history: os
    .meta(
      openapi({
        method: "GET",
        path: "/parties/{id}/history",
        summary:
          "List a party's vote share and seats in each general election for its polity's chambers and offices, one series per chamber or office",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.array(
        v.object({
          id: v.string(),
          name: v.string(),
          points: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
              date: v.string(),
              year: v.number(),
              races: v.number(),
              votes: v.nullable(v.number()),
              total: v.nullable(v.number()),
              seats: v.nullable(v.number()),
              filled: v.nullable(v.number()),
              predecessor: v.nullable(v.string()),
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const listed = await db.execute<{
        id: string;
        grp: string;
        seat: string;
        date: string;
        year: number;
        round: number | null;
        area: number | null;
        bucket: string;
        isList: boolean;
        isSingle: boolean;
        isCovering: boolean;
      }>(sql`
        with recursive ids(id) as (
          select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        ),
        home as (
          select coalesce(${input.within ?? null}::uuid, polity_id) as polity from party where id = (select id from ids limit 1)
        ),
        branches as (
          select pa.party_id as id from party_affiliation pa
          where pa.kind = 'regional_branch' and pa.parent_party_id in (select id from ids)
        ),
        family as (
          select id from ids union select id from branches
          union select em.entity_id from entity_merge em where em.into_id in (select id from branches)
        ),
        predecessors as (
          select coalesce(fm.into_id, r.from_party_id) as id
          from party_relation r
          left join entity_merge fm on fm.entity_id = r.from_party_id
          left join entity_merge tm on tm.entity_id = r.to_party_id
          where r.kind = 'merged_into' and coalesce(tm.into_id, r.to_party_id) in (select id from ids)
        ),
        early as materialized (
          select id, id as via from predecessors
          union select em.entity_id, p.id from entity_merge em join predecessors p on em.into_id = p.id
        ),
        ours as (select id from family union select id from early),
        candidates as materialized (
          select c.id, coalesce(e.parent_id, e.id) as grp, coalesce(c.body_id, c.office_id) as seat
          from contest c join election e on e.id = c.election_id
          left join body b on b.id = c.body_id
          left join office f on f.id = c.office_id
          where c.allocation_parent_id is null and c.party_scope_id is null and e.kind in ('general', 'local') and e.rerun_of_id is null
            and coalesce(c.body_id, c.office_id) is not null and not c.notional
            and ((select polity from home) is null or coalesce(b.polity_id, f.polity_id) = (select polity from home))
        ),
        entered as materialized (
          select distinct grp, seat from candidates k
          where exists (
            with recursive t(id) as (
              select k.id union all select c.id from contest c join t on c.allocation_parent_id = t.id
            )
            select from t join contest_option o on o.contest_id = t.id where o.party_id in (select id from ours)
          )
        ),
        roots as materialized (
          select c.id, n.grp, n.seat, e.date_from as date, ${CYCLE} as year, c.round_number as round, a.k as area,
            case when g.polity_id = (select polity from home) then n.grp::text else (${CYCLE})::text end as bucket,
            c.kind = 'list_allocation' as is_list,
            coalesce(c.rules_override->>'formula', es.rules->>'formula') = 'fptp' as is_single,
            coalesce(c.rules_override->'lists'->>'constituencies', es.rules->'lists'->>'constituencies') in ('all', 'covered') as is_covering
          from entered n join election g on g.id = n.grp join election e on coalesce(e.parent_id, e.id) = n.grp
          join contest c on c.election_id = e.id and coalesce(c.body_id, c.office_id) = n.seat
            and c.allocation_parent_id is null and c.party_scope_id is null and e.kind <> 'indirect'
            and e.date_from <= current_date
            and coalesce((select st.voting from seat st where st.id = c.seat_id), true)
            and (c.term is distinct from 'remainder' or (c.seat_id is not null and not exists (
              select from contest d where d.election_id = c.election_id and d.seat_id = c.seat_id and d.id <> c.id
            )))
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          left join electoral_system es on es.id = c.electoral_system_id
        )
        select id, grp, seat, date::text as date, year, round, area, bucket,
          is_list as "isList", coalesce(is_single, false) as "isSingle", coalesce(is_covering, false) as "isCovering"
        from roots
      `);
      const chunks = Map.groupBy(listed.rows, (_row, index) => index % 4)
        .values()
        .toArray();
      const counted = await Promise.all(
        chunks.map((chunk) =>
          db.execute<{
            root: string;
            votes: number | null;
            total: number | null;
            seats: number | null;
            filled: number | null;
            via: string | null;
          }>(sql`
            with recursive ids(id) as (
              select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
              union
              select em.entity_id from entity_merge em join ids on em.into_id = ids.id
            ),
            home as (
              select coalesce(${input.within ?? null}::uuid, polity_id) as polity from party where id = (select id from ids limit 1)
            ),
            branches as (
              select pa.party_id as id from party_affiliation pa
              where pa.kind = 'regional_branch' and pa.parent_party_id in (select id from ids)
            ),
            family as (
              select id from ids union select id from branches
              union select em.entity_id from entity_merge em where em.into_id in (select id from branches)
            ),
            predecessors as (
              select coalesce(fm.into_id, r.from_party_id) as id
              from party_relation r
              left join entity_merge fm on fm.entity_id = r.from_party_id
              left join entity_merge tm on tm.entity_id = r.to_party_id
              where r.kind = 'merged_into' and coalesce(tm.into_id, r.to_party_id) in (select id from ids)
            ),
            early as materialized (
              select id, id as via from predecessors
              union select em.entity_id, p.id from entity_merge em join predecessors p on em.into_id = p.id
            ),
            ours as (select id from family union select id from early),
            roots as materialized (
              select * from unnest(
                ${sql.param(chunk.map((row) => row.id))}::uuid[],
                ${sql.param(chunk.map((row) => row.round))}::int[],
                ${sql.param(chunk.map((row) => row.area))}::int[]
              ) as r(id, round, area)
            ),
            tree(root, id, area) as (
              select id, id, area from roots where round = 1
              union all
              select tree.root, c.id, tree.area from contest c join tree on c.allocation_parent_id = tree.id
            ),
            picked as materialized (
              select distinct on (rs.contest_id) t.root, rs.k, t.area
              from tree t cross join lateral (select * from result_set x where x.contest_id = t.id and x.status <> 'withdrawn' offset 0) rs
              cross join lateral (
                select from result_rollup x where x.result_set_k = rs.k and x.area_k = t.area and x.measure = any(${COUNTED}) limit 1
              ) hit
              order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last, rs.k
            ),
            fetched as (
              select p.root, x.option_k, x.measure, x.value::float8 as value, x.count_round,
                min(x.count_round) over (partition by p.root, x.measure) as first,
                min(array_position(${COUNTED}, x.measure)) over (partition by p.root) as main
              from picked p cross join lateral (
                select x.option_k, x.measure, x.value, x.count_round from result_rollup x
                where x.result_set_k = p.k and x.area_k = p.area and x.measure = any(${COUNTED}) and x.option_k <> 0
                offset 0
              ) x
            ),
            tallied as (
              select f.root, sum(f.value) as total, min(pe.via::text) as via,
                coalesce(sum(f.value) filter (
                  where o.party_id in (select id from ours)
                    or (select po.party_id from contest_option po where po.id = o.parent_option_id) in (select id from ours)
                ), 0) as votes
              from fetched f
              join contest_option o on o.k = f.option_k
              left join early pe on pe.id = o.party_id
              where f.count_round = f.first and array_position(${COUNTED}, f.measure) = f.main
              group by f.root
            ),
            seated as (
              select w.root, coalesce(sum(w.seats) filter (where w.mine), 0) as seats, sum(w.seats) as filled
              from (
                select r.id as root, o.id,
                  bool_or(
                    o.party_id in (select id from ours)
                    or o.party_id is null and ${fused(sql`o.id`, sql`o.contest_id`)} in (select id from family)
                  ) as mine,
                  max(coalesce(x.seats, 1)) as seats
                from roots r
                join lateral (
                  with recursive t(id) as (
                    select r.id union all select c.id from contest c join t on c.allocation_parent_id = t.id
                  )
                  select id from t
                ) sub on true
                join contest_option o on o.contest_id = sub.id
                join outcome x on x.option_id = o.id and x.kind in ('elected', 'won')
                join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
                where (not exists (select from contest k where k.allocation_parent_id = sub.id)
                  or sub.id = r.id and not exists (select from contest k where k.allocation_parent_id = r.id))
                  and not (o.kind = 'list' and ${pooled(sql`o.id`)})
                  and not (o.parent_option_id is not null and not ${pooled(sql`o.parent_option_id`)} and exists (
                    select from outcome y join result_set ry on ry.id = y.result_set_id and ${latest("ry")}
                    where y.option_id = o.parent_option_id and y.kind in ('elected', 'won') and y.candidacy_id is null
                  ))
                group by r.id, o.id
              ) w
              group by w.root
            )
            select r.id as root, t.votes::float8 as votes, t.total::float8 as total, s.seats::int as seats,
              s.filled::int as filled, t.via
            from roots r left join tallied t on t.root = r.id left join seated s on s.root = r.id
          `),
        ),
      );
      const byRoot = new Map(
        counted.flatMap((result) =>
          result.rows.map((row) => [row.root, row] as const),
        ),
      );
      const found = {
        rows: Map.groupBy(listed.rows, (row) => `${row.bucket} ${row.seat}`)
          .values()
          .map((group) => {
            const isMixed = group.some((row) => row.isList);
            const isCovered = group.some((row) => row.isList && row.isCovering);
            const sum = (
              pick: (
                row: (typeof counted)[number]["rows"][number],
              ) => number | null,
              isSkipping: boolean,
            ) => {
              let total: number | null = null;
              for (const row of group) {
                if (isSkipping && row.isSingle) {
                  continue;
                }
                const tally = byRoot.get(row.id);
                const value = tally == null ? null : pick(tally);
                if (value != null) {
                  total = (total ?? 0) + value;
                }
              }
              return total;
            };
            const grps = new Set(group.map((row) => row.grp));
            const vias = group
              .flatMap((row) => byRoot.get(row.id)?.via ?? [])
              .toSorted((a, b) => Number(a > b) - Number(a < b));
            return {
              grp: grps.size === 1 ? (group[0]?.grp ?? null) : null,
              seat: group[0]?.seat ?? "",
              root:
                group
                  .map((row) => row.id)
                  .toSorted((a, b) => Number(a > b) - Number(a < b))[0] ?? "",
              date:
                group
                  .map((row) => row.date)
                  .toSorted((a, b) => Number(a > b) - Number(a < b))[0] ?? "",
              year: Math.min(...group.map((row) => row.year)),
              races: new Set(group.map((row) => row.id)).size,
              votes: sum((row) => row.votes, isMixed),
              total: sum((row) => row.total, isMixed),
              seats: sum((row) => row.seats, isCovered),
              filled: sum((row) => row.filled, isCovered),
              predecessor: group
                .filter((row) => (byRoot.get(row.id)?.votes ?? 0) > 0)
                .every((row) => byRoot.get(row.id)?.via != null)
                ? (vias[0] ?? null)
                : null,
            };
          })
          .toArray()
          .toSorted((a, b) => a.date.localeCompare(b.date)),
      };
      const seats = [...new Set(found.rows.map((row) => row.seat))];
      const linked = found.rows.flatMap((row) => [
        ...(row.races === 1 ? [row.root] : []),
        ...(row.grp == null ? [] : [row.grp]),
      ]);
      const local = await db.execute<{
        id: string;
        election: string;
        path: string;
        tail: string;
        date: string;
        year: number;
        votes: number | null;
        total: number | null;
        seats: number | null;
        filled: number | null;
      }>(sql`
        with recursive ids(id) as (
          select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        ),
        roots as materialized (
          select c.id, c.election_id as election, s.path, substring(s.path from '[^/]+$') as tail, e.date_from, a.k as area
          from contest c join election e on e.id = c.election_id
          join slug s on s.entity_id = c.id and s.is_current
          join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = pt.area_id
          where e.polity_id = coalesce(${input.within ?? null}::uuid, (select polity_id from party where id = (select id from ids limit 1)))
            and e.kind = 'local' and c.kind = 'body_seats'
            and c.body_id is null and c.office_id is null and c.allocation_parent_id is null
        ),
        counted as (
          select r.id, sum(x.value) filter (where o.party_id in (select id from ids))::float8 as votes,
            sum(x.value)::float8 as total
          from roots r join result_set rs on rs.contest_id = r.id and ${latest("rs")}
          cross join lateral (
            select x.option_k, x.value from result_rollup x
            where x.result_set_k = rs.k and x.area_k = r.area and x.measure = 1 and x.option_k <> 0 offset 0
          ) x
          join contest_option o on o.k = x.option_k
          group by r.id
        ),
        won as (
          select o.contest_id as id, sum(x.seats) filter (where o.party_id in (select id from ids))::int as seats,
            sum(x.seats)::int as filled
          from roots r join contest_option o on o.contest_id = r.id
          cross join lateral (
            select max(x.seats) as seats from outcome x
            join result_set rs on rs.id = x.result_set_id and ${latest("rs")}
            where x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
            having count(*) > 0
          ) x
          group by o.contest_id
        )
        select r.id, r.election, r.path, r.tail, r.date_from::text as date, extract(year from r.date_from)::int as year,
          c.votes, c.total, w.seats, w.filled
        from roots r left join counted c on c.id = r.id left join won w on w.id = r.id
        order by r.date_from
      `);
      const [labels, slugs] = await Promise.all([
        names([
          ...seats,
          ...linked,
          ...found.rows.flatMap((row) => row.predecessor ?? []),
          ...local.rows.flatMap((row) => [row.id, row.election]),
        ]),
        paths(linked),
      ]);
      const tallies = Map.groupBy(local.rows, (row) => row.tail)
        .entries()
        .filter(([, rows]) => rows.some((row) => (row.votes ?? 0) > 0))
        .map(([tail, rows]) => ({
          id: tail,
          name:
            labels.full
              .get(rows.at(-1)?.id ?? "")
              ?.split(", ")
              .at(-1) ?? tail,
          points: rows
            .filter((row) => row.votes != null)
            .map((row) => ({
              name: labels.full.get(row.election) ?? row.path,
              path: row.path,
              date: row.date,
              year: row.year,
              races: 1,
              votes: row.total == null || row.total === 0 ? null : row.votes,
              total: row.total == null || row.total === 0 ? null : row.total,
              seats: row.filled == null ? null : (row.seats ?? 0),
              filled: row.filled,
              predecessor: null,
            })),
        }))
        .toArray();
      return [
        ...seats.map((seat) => ({
          id: seat,
          name: labels.full.get(seat) ?? "",
          points: found.rows
            .filter((row) => row.seat === seat)
            .filter((row, index, rows) => {
              const nearest = Math.max(
                rows[index - 1]?.races ?? 0,
                rows[index + 1]?.races ?? 0,
              );
              return nearest < 10 || row.races * 2 >= nearest;
            })
            .map((row) => {
              const target = row.races === 1 ? row.root : row.grp;
              return {
                name:
                  (target == null ? null : labels.full.get(target)) ??
                  (row.grp == null ? null : labels.full.get(row.grp)) ??
                  `${row.year} ${labels.full.get(seat) ?? ""}`.trim(),
                path:
                  (target == null ? null : slugs.get(target)) ??
                  (row.grp == null ? null : slugs.get(row.grp)) ??
                  null,
                date: row.date,
                year: row.year,
                races: row.races,
                votes: row.total == null || row.total === 0 ? null : row.votes,
                total: row.total == null || row.total === 0 ? null : row.total,
                seats: row.filled == null ? null : row.seats,
                filled: row.filled,
                predecessor:
                  row.predecessor == null
                    ? null
                    : (labels.full.get(row.predecessor) ?? null),
              };
            }),
        })),
        ...tallies,
      ];
    }),
  locate: os
    .meta(
      openapi({
        method: "GET",
        path: "/parties/locate",
        summary:
          "Find the party a /parties/{polity}/{slug} path names, the polity given by its path",
      }),
    )
    .input(v.object({ polity: v.optional(v.string()), slug: v.string() }))
    .output(v.object({ id: v.string() }))
    .handler(async ({ input }) => {
      const owner =
        input.polity == null
          ? null
          : await db.execute<{ id: string }>(sql`
            select s.entity_id as id from slug s join polity p on p.id = s.entity_id
            where s.path = ${input.polity} and s.is_current
          `);
      if (owner != null && owner.rows[0] == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const id = await locate("party", input.slug, owner?.rows[0]?.id ?? null);
      if (id == null) {
        throw new ORPCError("NOT_FOUND");
      }
      return { id };
    }),
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/parties/{id}",
        summary: "Get a party, its lineage, and its years on the ballot",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        within: v.optional(v.pipe(v.string(), v.uuid())),
      }),
    )
    .output(
      v.object({
        name: v.string(),
        short: v.nullable(v.string()),
        color: v.nullable(v.string()),
        kind: v.string(),
        polity: v.nullable(Link),
        wikidata: v.nullable(v.string()),
        span: v.nullable(
          v.object({ from: v.string(), to: v.string(), contests: v.number() }),
        ),
        relations: v.array(
          v.object({
            kind: v.string(),
            outgoing: v.boolean(),
            date: v.nullable(v.string()),
            party: PartyRef,
            span: v.nullable(v.object({ from: v.string(), to: v.string() })),
          }),
        ),
        parents: v.array(v.object({ kind: v.string(), party: PartyRef })),
        events: v.optional(
          v.array(v.object({ kind: v.string(), date: v.nullable(v.string()) })),
        ),
        names: v.optional(
          v.array(
            v.object({
              name: v.string(),
              short: v.nullable(v.string()),
              from: v.nullable(v.string()),
              to: v.nullable(v.string()),
            }),
          ),
        ),
        namesakes: v.optional(
          v.array(
            v.object({
              party: PartyRef,
              span: v.nullable(v.object({ from: v.string(), to: v.string() })),
            }),
          ),
        ),
        scope: v.nullable(
          v.object({
            polity: v.object({
              id: v.string(),
              name: v.string(),
              path: v.nullable(v.string()),
            }),
            branches: v.array(PartyRef),
            span: v.nullable(
              v.object({
                from: v.string(),
                to: v.string(),
                contests: v.number(),
              }),
            ),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        kind: string;
        polity: string | null;
        wikidata: string | null;
        from: string | null;
        to: string | null;
        contests: number;
      }>(sql`
        with target as (
          select p.id, p.kind::text as kind, p.polity_id as polity from party p
          where p.id = coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
        ),
        ids as (
          select id from target union select entity_id from entity_merge where into_id = (select id from target)
        )
        select t.id, t.kind, t.polity,
          (select x.value from external_id x where x.entity_id = t.id and x.scheme = 'wikidata' limit 1) as wikidata,
          s.from, s.to, s.contests
        from target t cross join lateral (
          select min(e.date_from)::text as from, max(e.date_from)::text as to, count(distinct o.contest_id)::int as contests
          from contest_option o join contest c on c.id = o.contest_id join election e on e.id = c.election_id
          where o.party_id in (select id from ids) and not c.notional
        ) s
      `);
      const party = found.rows[0];
      if (party == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const [relations, parents] = await Promise.all([
        db.execute<{
          kind: string;
          outgoing: boolean;
          date: string | null;
          other: string | null;
        }>(sql`
          select r.kind::text, r.from_party_id = ${party.id}::uuid as outgoing,
            coalesce(r.date, e.date_from)::text as date,
            case when r.from_party_id = ${party.id}::uuid then r.to_party_id else r.from_party_id end as other
          from party_relation r left join election e on e.id = r.election_id
          where r.from_party_id = ${party.id}::uuid or r.to_party_id = ${party.id}::uuid
          order by 3 nulls last
        `),
        db.execute<{ kind: string; parent: string }>(sql`
          select distinct on (parent_party_id) kind::text, parent_party_id as parent
          from party_affiliation where party_id = ${party.id}::uuid
          order by parent_party_id, upper(valid) desc nulls first
        `),
      ]);
      const scoped =
        input.within == null
          ? null
          : await db.execute<{
              from: string | null;
              to: string | null;
              contests: number;
              branches: Array<string>;
            }>(sql`
              with recursive below(id) as (
                select ${input.within}::uuid
                union
                select pp.polity_id from polity_parent pp join below on pp.parent_id = below.id
              ),
              branches as (
                select pa.party_id as id from party_affiliation pa join party bp on bp.id = pa.party_id
                where pa.kind = 'regional_branch' and pa.parent_party_id = ${party.id}::uuid
                  and bp.polity_id in (select id from below)
              ),
              family as (
                select ${party.id}::uuid as id
                union select entity_id from entity_merge where into_id = ${party.id}::uuid
                union select id from branches
                union select em.entity_id from entity_merge em where em.into_id in (select id from branches)
              )
              select min(e.date_from)::text as from, max(e.date_from)::text as to,
                count(distinct o.contest_id)::int as contests,
                array(select id::text from branches) as branches
              from contest_option o join contest c on c.id = o.contest_id join election e on e.id = c.election_id
              where o.party_id in (select id from family) and e.polity_id in (select id from below) and not c.notional
            `);
      const branched = scoped?.rows[0]?.branches ?? [];
      const polity = [
        ...(party.polity == null ? [] : [party.polity]),
        ...(input.within == null ? [] : [input.within]),
      ];
      const own = await names([party.id]);
      const sakes = await namesakes(
        "party",
        segment(own.full.get(party.id) ?? ""),
        party.polity,
      );
      const peers = sakes.filter((id) => id !== party.id);
      const others = [
        ...relations.rows.flatMap((row) => row.other ?? []),
        ...peers,
      ];
      const [entities, slugs, spans, named] = await Promise.all([
        refs([
          party.id,
          ...polity,
          ...others,
          ...parents.rows.map((row) => row.parent),
          ...branched,
        ]),
        paths(polity),
        db.execute<{ party: string; from: string; to: string }>(sql`
          select coalesce(m.into_id, o.party_id) as party, min(e.date_from)::text as from, max(e.date_from)::text as to
          from contest_option o
          left join entity_merge m on m.entity_id = o.party_id
          join contest c on c.id = o.contest_id join election e on e.id = c.election_id
          where coalesce(m.into_id, o.party_id) = any(${sql.param(others)}::uuid[])
          group by 1
        `),
        renames(party.id),
      ]);
      const self = entities.get(party.id);
      const ranges = new Map(spans.rows.map((row) => [row.party, row]));
      return {
        name: self?.label ?? "",
        short: self?.short ?? null,
        color: self?.color ?? null,
        kind: party.kind,
        polity:
          party.polity == null
            ? null
            : {
                name: entities.get(party.polity)?.label ?? "",
                path: slugs.get(party.polity) ?? null,
              },
        wikidata: party.wikidata,
        span:
          party.from == null || party.to == null
            ? null
            : { from: party.from, to: party.to, contests: party.contests },
        relations: relations.rows.flatMap((row) => {
          if (row.other == null) {
            return [];
          }
          const other = entities.get(row.other);
          const range = ranges.get(row.other);
          return other == null
            ? []
            : [
                {
                  kind: row.kind,
                  outgoing: row.outgoing,
                  date: row.date,
                  party: other,
                  span:
                    range == null ? null : { from: range.from, to: range.to },
                },
              ];
        }),
        parents: parents.rows.flatMap((row) => {
          const other = entities.get(row.parent);
          return other == null ? [] : [{ kind: row.kind, party: other }];
        }),
        names: named,
        events: relations.rows.flatMap((row) =>
          row.other == null ? [{ kind: row.kind, date: row.date }] : [],
        ),
        namesakes: peers.flatMap((id) => {
          const other = entities.get(id);
          const range = ranges.get(id);
          return other == null
            ? []
            : [
                {
                  party: other,
                  span:
                    range == null ? null : { from: range.from, to: range.to },
                },
              ];
        }),
        scope:
          input.within == null
            ? null
            : {
                polity: {
                  id: input.within,
                  name: entities.get(input.within)?.label ?? "",
                  path: slugs.get(input.within) ?? null,
                },
                branches: branched.flatMap((id) => entities.get(id) ?? []),
                span:
                  scoped?.rows[0]?.from == null || scoped.rows[0].to == null
                    ? null
                    : {
                        from: scoped.rows[0].from,
                        to: scoped.rows[0].to,
                        contests: scoped.rows[0].contests,
                      },
              },
      };
    }),
};

export const search = {
  query: os
    .meta(
      openapi({
        method: "GET",
        path: "/search",
        summary:
          "Search polities, chambers, elections, contests, parties, and people by name, accents and case ignored",
      }),
    )
    .input(
      v.object({
        q: v.pipe(v.string(), v.trim(), v.maxLength(200)),
        limit: v.optional(
          v.pipe(
            v.union([v.number(), v.string()]),
            v.transform(Number),
            v.integer(),
            v.minValue(1),
            v.maxValue(50),
          ),
          12,
        ),
      }),
    )
    .output(
      v.array(
        v.object({
          id: v.string(),
          type: v.string(),
          name: v.string(),
          path: v.nullable(v.string()),
          context: v.nullable(v.string()),
          date: v.nullable(v.string()),
          photo: v.nullable(v.string()),
          logo: v.nullable(LogoRef),
          color: v.nullable(v.string()),
          office: v.nullable(v.string()),
          ballot: v.nullable(v.object({ date: v.string(), label: v.string() })),
          isBest: v.boolean(),
          isSimilar: v.boolean(),
        }),
      ),
    )
    .handler(async ({ input }) => {
      if (input.q === "") {
        return [];
      }
      const ALIASES: Record<string, string> = {
        uk: "united kingdom",
        gb: "united kingdom",
        britain: "united kingdom",
        "great britain": "united kingdom",
        us: "united states",
        usa: "united states",
        "u.s.": "united states",
        "u.k.": "united kingdom",
        eu: "european union",
        nyc: "new york city",
        dc: "district of columbia",
        "washington dc": "district of columbia",
      };
      const district = /^([a-z]{2})[-\s]?(\d{1,2}|al)$/iu.exec(input.q);
      const states =
        district == null
          ? null
          : await db.execute<{ name: string }>(sql`
            select n.text as name from polity p join name n on n.entity_id = p.id and n.kind = 'official'
            where p.iso_code = ${`US-${district[1]?.toUpperCase() ?? ""}`}
            order by n.is_preferred desc, n.recorded_from desc, n.id limit 1
          `);
      const state = states?.rows[0]?.name;
      const seat = Number(district?.[2]);
      const units = (
        await db.execute<{ code: string; name: string; label: string }>(sql`
          select lower(substr(p.iso_code, 4)) as code, lower(n.text) as name, n.text as label
          from polity p join name n on n.entity_id = p.id and n.kind = 'official' and n.is_preferred
          where p.iso_code like 'US-%'
        `)
      ).rows;
      const lowered = input.q.toLowerCase();
      const unit =
        district != null ||
        units.some((row) => row.name === lowered) ||
        ALIASES[lowered.replaceAll(".", "").replace(",", "")] != null
          ? undefined
          : units
              .filter((row) =>
                [row.name, row.code].some((label) =>
                  new RegExp(
                    `.(?:,\\s*|\\s+)${RegExp.escape(label)}$`,
                    "u",
                  ).test(lowered),
                ),
              )
              .toSorted((a, b) => b.name.length - a.name.length)[0];
      const asked =
        unit == null
          ? input.q
          : input.q
              .slice(
                0,
                lowered.endsWith(unit.name)
                  ? -unit.name.length
                  : -unit.code.length,
              )
              .replace(/[,\s]+$/u, "");
      const room = unit == null ? input.limit : 50;
      const q =
        state == null
          ? asked
              .replace(
                /\b(c?isd|usd)\b/giu,
                (word) =>
                  ({
                    isd: "independent school district",
                    cisd: "consolidated independent school district",
                    usd: "unified school district",
                  })[word.toLowerCase()] ?? word,
              )
              .replace(
                /^(?:washington,? d\.?c\.?|great britain|u\.s\.|u\.k\.|uk|gb|britain|usa|us|eu|nyc|dc)(?=\s|$)/iu,
                (word) =>
                  ALIASES[word.toLowerCase()] ??
                  ALIASES[
                    word.toLowerCase().replaceAll(".", "").replace(",", "")
                  ] ??
                  word,
              )
          : `${state}'s ${
              Number.isNaN(seat)
                ? "at-large"
                : `${seat}${{ one: "st", two: "nd", few: "rd", other: "th", zero: "th", many: "th" }[new Intl.PluralRules("en", { type: "ordinal" }).select(seat)]}`
            } congressional district`;
      const bodyRows = db.execute<{
        id: string;
        polity: string;
        isClose: boolean;
      }>(sql`
        with q as (select lower(unaccent(${q})) as t),
        seated as (select body_id, count(*) as members from tenure group by body_id)
        select b.id, b.polity_id as polity,
          bool_or(
            lower(unaccent(n.text)) like '%' || q.t || '%'
            and similarity(q.t, lower(unaccent(n.text))) >= 0.3
          ) as "isClose"
        from body b join name n on n.entity_id = b.id cross join q
        left join seated on seated.body_id = b.id
        where b.kind <> 'electoral_college' and (
          lower(unaccent(n.text)) like '%' || q.t || '%' or q.t <% lower(unaccent(n.text))
        )
        group by b.id, b.polity_id, seated.members
        order by max(word_similarity(q.t, lower(unaccent(n.text)))) desc,
          coalesce(seated.members, 0) desc
        limit 4
      `);
      const found = await db.execute<{
        id: string;
        type: string;
        target: string | null;
        context: string | null;
        date: string | null;
        boost: number | null;
        office: string | null;
        isExecutive: boolean | null;
        matched: boolean;
      }>(sql`
        with q as (select lower(unaccent(${q})) as t),
        terms as (
          select to_tsquery('simple', string_agg(quote_literal(w) || case when n = last then ':*' else '' end, ' & ' order by n)) as tq
          from (
            select w, n, max(n) over () as last
            from q, regexp_split_to_table(q.t, '\\s+') with ordinality as t(w, n) where w <> ''
          ) words
        ),
        strict as materialized (
          select d.entity_id, d.type, ts_rank(d.tsv, terms.tq) + 0.5 + 0.5 * word_similarity(q.t, d.text) + 0.5 * (d.tsv @@ plainto_tsquery('simple', q.t))::int + d.rank_boost * 0.15 as score, true as matched,
            d.tsv @@ plainto_tsquery('simple', q.t) as exact
          from search_document d, q, terms
          where d.tsv @@ terms.tq
        ),
        hits as (
          select * from strict
          union all
          select d.entity_id, d.type, word_similarity(q.t, d.text) + d.rank_boost * 0.15, false, false
          from search_document d, q, terms
          where q.t <% d.text and not d.tsv @@ terms.tq
            and (select count(*) from (select from strict limit ${room * 2}) enough) < ${room * 2}
        ),
        pool as (select * from hits order by score desc limit ${q.length < 3 ? 1000 : 50_000}),
        seats as (
          select c.id, array_agg(t.polity_id) as polities
          from pool h join contest c on c.id = h.entity_id join election el on el.id = c.election_id
          join polity_territory t on t.area_id = c.district_area_id
          where h.type = 'contest' and el.date_from >= coalesce(lower(t.valid), '-infinity') and el.date_from < coalesce(upper(t.valid), 'infinity')
          group by c.id
        ),
        tiered as (
          select h.entity_id, h.type, h.matched, coalesce(el.polity_id, case when h.type = 'polity' then h.entity_id end) as polity_id, el.date_from, h.score
            + case
              when not h.matched then 0
              when h.type = 'polity' then 0.75 * h.exact::int
              when h.type = 'election' then (el.kind = 'general' and q.t ~ '\\m\\d{4}\\M')::int
              when h.type = 'party' then coalesce((
                select (0.75 * h.exact::int * least(1, greatest(0, d.rank_boost - 2) / 1.5) + 0.3 * greatest(0, d.rank_boost - 2))
                from search_document d where d.entity_id = h.entity_id), 0)
              when h.type = 'person' then coalesce((
                select 0.75 * h.exact::int * least(1, greatest(0, d.rank_boost - 2) / 3)
                from search_document d where d.entity_id = h.entity_id), 0)
              when h.type <> 'contest' or c.kind = 'ballot_question' or c.allocation_parent_id is not null then 0
              when f.whole then
                (0.5 + 0.3 * (
                  coalesce(o.kind in ('head_of_state', 'head_of_government', 'governor', 'mayor'), false)
                  or c.kind = 'elector_slate' or f.federal
                )::int) * case when el.kind in ('primary', 'caucus') then 0.5 else 1 end
              when f.federal then 0.25
              else 0
            end as score
          from pool h cross join q
          left join contest c on h.type = 'contest' and c.id = h.entity_id
          left join election el on el.id = coalesce(c.election_id, case when h.type = 'election' then h.entity_id end)
          left join body b on b.id = c.body_id
          left join office o on o.id = c.office_id
          left join polity_territory pt on pt.polity_id = el.polity_id and pt.valid @> el.date_from
          left join seats on seats.id = c.id
          cross join lateral (
            select
              c.district_area_id is null or c.district_area_id = pt.area_id or seats.id is not null as whole,
              exists (
                select from polity_parent p1 left join polity_parent p2 on p2.polity_id = p1.parent_id
                where b.polity_id in (p1.parent_id, p2.parent_id)
                  and (p1.polity_id = el.polity_id or p1.polity_id = any(seats.polities))
              ) as federal
          ) f
          order by score desc nulls last, el.date_from desc nulls last limit 200
        ),
        top as (
          select t.entity_id, t.type, t.matched, t.score
            + (t.matched and q.t like '% %' and exists (
              select from name n where n.entity_id = t.entity_id and n.kind <> 'short' and lower(unaccent(n.text)) = q.t
            ))::int
            + case
              when not t.matched then 0
              when t.type = 'person' then coalesce((
                select 0.1 * ln(1 + count(*)) + 0.15 * bool_or(tn.office_id is not null)::int
                from tenure tn where tn.person_id = t.entity_id), 0)
                + 0.2 * exists (select from external_id z where z.entity_id = t.entity_id and z.scheme = 'wikidata')::int
                + coalesce((
                select 0.2 * greatest(0, least(1, (extract(year from max(e.date_from)) - 1850) / 176))
                from candidacy cy join contest_option o on o.id = cy.option_id
                join contest c on c.id = o.contest_id join election e on e.id = c.election_id
                where cy.person_id = t.entity_id), 0)
              when t.polity_id is not null then coalesce((
                select case when y.kind in ('sovereign_state', 'supranational') then 0.3 when y.kind = 'federal_unit' then 0.25 end
                from polity y where y.id = t.polity_id), (
                select least(0.3, greatest(0, 0.1 * (log(greatest(max(g.population), 1)) - 4)))
                from coverage_government g where g.polity_id = t.polity_id), 0)
              else 0
            end as score
          from tiered t cross join q
          order by score desc nulls last, t.date_from desc nulls last limit ${room * 2}
        )
        select top.entity_id as id, top.type::text as type, top.matched, x.target, x.context,
          coalesce(x.date, (select e.date_from from contest c join election e on e.id = c.election_id where c.id = x.target and top.type = 'person'))::text as date,
          case when top.type = 'person' then (select d.rank_boost from search_document d where d.entity_id = top.entity_id) end as boost,
          best.id as office, best.rank >= 2 as "isExecutive"
        from top
        left join lateral (
            select held.id, held.rank from (
              select coalesce(c.office_id, c.body_id) as id, el.date_from as at,
                case when f.kind = 'mayor' then 0 when f.kind = 'other' then 2 when fp.kind in ('sovereign_state', 'supranational') then 3
                  when f.id is not null then 2 when b.kind = 'upper_house' then 1 else 0 end as rank
              from candidacy cy
              join outcome oc on oc.option_id = cy.option_id and oc.kind in ('elected', 'won')
                and (oc.kind = 'elected' or not exists (select from outcome w where w.result_set_id = oc.result_set_id and w.seats > oc.seats))
              join contest_option o on o.id = cy.option_id
              join contest c on c.id = o.contest_id and c.party_scope_id is null
              join election el on el.id = c.election_id and el.kind <> 'primary'
              left join office f on f.id = c.office_id left join polity fp on fp.id = f.polity_id
              left join body b on b.id = c.body_id left join polity bp on bp.id = b.polity_id
              where cy.person_id = top.entity_id and cy.role = 'head' and (
                (f.kind in ('head_of_state', 'head_of_government', 'governor', 'mayor')
                  or f.kind = 'other' and fp.kind in ('sovereign_state', 'supranational')) and el.polity_id = f.polity_id
                or f.id is null and b.kind in ('upper_house', 'lower_house', 'unicameral') and bp.kind = 'sovereign_state')
              union all
              select t.office_id, t.from_date, case when fp.kind in ('sovereign_state', 'supranational') then 3 else 2 end
              from tenure t join office f on f.id = t.office_id join polity fp on fp.id = f.polity_id
              where t.person_id = top.entity_id and f.kind in ('head_of_state', 'head_of_government', 'governor')
            ) held
            order by held.rank desc, held.at desc nulls last
            limit 1) best on top.type = 'person'
        cross join lateral (
          select
            case top.type
              when 'person' then (
                select case when c.kind = 'list_allocation' then c.id else coalesce(c.allocation_parent_id, c.id) end
                from candidacy cy
                join contest_option o on o.id = cy.option_id join contest c on c.id = o.contest_id
                join election e on e.id = c.election_id
                where cy.person_id = top.entity_id order by o.kind = 'write_in', extract(year from e.date_from) desc nulls last,
                  e.kind = 'indirect', e.date_from desc limit 1)
              when 'party' then (
                select top.entity_id where exists (
                  select from contest_option o join contest c on c.id = o.contest_id
                  where o.party_id = top.entity_id and c.allocation_parent_id is null))
              else top.entity_id
            end as target,
            case top.type
              when 'contest' then (select c.election_id from contest c where c.id = top.entity_id)
              when 'election' then (select coalesce(e.parent_id, e.polity_id) from election e where e.id = top.entity_id)
              when 'polity' then (select pp.parent_id from polity_parent pp where pp.polity_id = top.entity_id limit 1)
              when 'party' then (select polity_id from party where id = top.entity_id)
            end as context,
            case top.type
              when 'contest' then (select e.date_from from contest c join election e on e.id = c.election_id where c.id = top.entity_id)
              when 'election' then (select date_from from election where id = top.entity_id)
            end as date
        ) x
        order by top.score desc, x.date desc nulls last
      `);
      const matched = await bodyRows;
      const bodies = matched.rows.map((row) => ({
        id: row.id,
        type: "body",
        target: row.id,
        context: row.polity,
        date: null,
        boost: null,
        office: null,
        isExecutive: null,
        matched: true,
        isClose: row.isClose,
      }));
      const rows = [
        ...bodies.filter((row) => row.isClose),
        ...found.rows.filter((row) => row.target != null),
        ...bodies.filter((row) => !row.isClose),
      ];
      const upcoming = new Map(
        (
          await db.execute<{ person: string; date: string; seat: string }>(sql`
            select distinct on (cy.person_id) cy.person_id as person, e.date_from::text as date, coalesce(c.office_id, c.body_id) as seat
            from candidacy cy
            join contest_option o on o.id = cy.option_id
            join contest c on c.id = o.contest_id and c.party_scope_id is null and coalesce(c.office_id, c.body_id) is not null
            join election e on e.id = c.election_id and e.kind <> 'primary' and e.status = 'scheduled' and e.date_from >= current_date
            where cy.person_id = any(${sql.param(rows.flatMap((row) => (row.type === "person" ? [row.id] : [])))}::uuid[]) and cy.role = 'head'
            order by cy.person_id, e.date_from
          `)
        ).rows.map((row) => [row.person, row]),
      );
      const ids = rows.flatMap((row) =>
        [
          row.id,
          row.target,
          row.context,
          row.office,
          upcoming.get(row.id)?.seat,
        ].filter((id) => id != null),
      );
      const [labels, slugs, faces, peers] = await Promise.all([
        names(
          ids,
          null,
          rows.filter((row) => row.type === "person").map((row) => row.id),
        ),
        paths(ids),
        portraits(
          rows.filter((row) => row.type === "person").map((row) => row.id),
        ),
        Promise.all(
          [...new Set(matched.rows.map((row) => row.polity))].map((polity) =>
            chambers(polity),
          ),
        ).then(
          (found) =>
            new Map(found.flat().map((peer) => [peer.id, peer.slug] as const)),
        ),
      ]);
      const ranked = rows
        .map((row) => {
          const next = upcoming.get(row.id);
          return {
            id: row.id,
            type: row.type,
            name: labels.full.get(row.id) ?? "",
            path:
              row.type === "body" &&
              slugs.get(row.context ?? "") != null &&
              peers.get(row.id) != null
                ? `chambers/${slugs.get(row.context ?? "")}/${peers.get(row.id)}`
                : ["person", "party", "body"].includes(row.type)
                  ? `id/${row.id}`
                  : (slugs.get(row.target ?? "") ?? null),
            context:
              [
                labels.full.get(
                  (row.type === "person"
                    ? row.isExecutive === true
                      ? row.office
                      : row.target
                    : row.context) ?? "",
                ),
                row.type === "body"
                  ? units.find(
                      (entry) =>
                        entry.code ===
                        /^us\/([a-z]{2})\/./u.exec(
                          slugs.get(row.context ?? "") ?? "",
                        )?.[1],
                    )?.label
                  : undefined,
              ]
                .filter((part) => part != null)
                .join(", ") || null,
            date: row.isExecutive === true ? null : row.date,
            photo: faces.get(row.id)?.src ?? null,
            office: labels.full.get(row.office ?? "") ?? null,
            ballot:
              next == null
                ? null
                : { date: next.date, label: labels.full.get(next.seat) ?? "" },
            boost: row.boost,
            isSimilar: !row.matched,
          };
        })
        .filter((row) => row.path != null && row.name !== "")
        .toSorted(
          (a, b) =>
            Number(
              !["person", "party"].includes(b.type) &&
                (segment(b.name) === segment(q) ||
                  (b.type === "polity" &&
                    segment(b.name.replace(/^City of /u, "")) === segment(q)) ||
                  (b.type === "polity" &&
                    !b.name.includes(" ") &&
                    segment(b.name.split("-")[0] ?? "") === segment(q))),
            ) -
            Number(
              !["person", "party"].includes(a.type) &&
                (segment(a.name) === segment(q) ||
                  (a.type === "polity" &&
                    segment(a.name.replace(/^City of /u, "")) === segment(q)) ||
                  (a.type === "polity" &&
                    !a.name.includes(" ") &&
                    segment(a.name.split("-")[0] ?? "") === segment(q))),
            ),
        )
        .toSorted((a, b) =>
          unit == null
            ? 0
            : Number(
                b.path === `us/${unit.code}` ||
                  b.path?.startsWith(`us/${unit.code}/`) === true ||
                  b.path?.includes(`/us/${unit.code}/`) === true,
              ) -
              Number(
                a.path === `us/${unit.code}` ||
                  a.path?.startsWith(`us/${unit.code}/`) === true ||
                  a.path?.includes(`/us/${unit.code}/`) === true,
              ),
        )
        .slice(0, input.limit);
      const people = ranked.filter((row) => row.type === "person");
      const [first, second] = people;
      const words = segment(q).split("-");
      const best =
        first != null &&
        (first.boost ?? 0) >= 4 &&
        (second?.boost ?? 0) <= (first.boost ?? 0) - 1 &&
        words.every((word) => segment(first.name).split("-").includes(word))
          ? first
          : null;
      const parties = ranked
        .filter((row) => row.type === "party")
        .map((row) => row.id);
      const [marks, colors] = await Promise.all([
        logos(parties, null),
        db.execute<{ id: string; color: string }>(sql`
          select distinct on (pc.party_id) pc.party_id as id, pc.color from party_color pc
          where pc.party_id = any(${sql.param(parties)}::uuid[])
          order by pc.party_id, upper_inf(pc.valid) desc, upper(pc.valid) desc
        `),
      ]);
      const painted = new Map(colors.rows.map((row) => [row.id, row.color]));
      const located = new Map(
        await Promise.all(
          people.map(async (row) => {
            const { path } = await call(entities.path, { id: row.id });
            return [row.id, path] as const;
          }),
        ),
      );
      return [
        ...(best == null ? [] : [best]),
        ...ranked.filter((row) => row !== best),
      ].map((row) => ({
        id: row.id,
        type: row.type,
        name: row.name,
        path: located.get(row.id) ?? row.path,
        context: row.context,
        date: row.date,
        photo: row.photo,
        logo: marks[row.id] ?? null,
        color: painted.get(row.id) ?? null,
        office: row.office,
        ballot: row.ballot,
        isBest: row === best,
        isSimilar: row.isSimilar,
      }));
    }),
};
