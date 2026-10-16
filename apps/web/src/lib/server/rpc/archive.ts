import type { Grouping, Match, Option, Relation, Side } from "@ea/analytics";
import type { SQL } from "drizzle-orm";
import { ancestry, compare } from "@ea/analytics";
import { openapi } from "@orpc/openapi";
import { ORPCError, os } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { db } from "#lib/server/db/index.js";

const STAGES = sql.raw(
  "array['court_revised','recount','certified','official','preliminary','live','alternative']::stage[]",
);

const MEASURES = new Map([
  [1, "votes"],
  [8, "list_votes"],
  [11, "electoral_votes"],
  [12, "state_delegate_equivalents"],
]);
const COUNTED = sql`${sql.param(MEASURES.keys().toArray())}::int2[]`;
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
    join result_set rs on rs.contest_id = ch.contest_id and rs.is_default
    join result_rollup x on x.result_set_k = rs.k and x.option_k = ch.k
    where ch.contest_id = ${contest} and ch.parent_option_id = ${option} and ch.party_id is not null
    group by ch.id, ch.party_id order by max(x.value) desc, ch.id limit 1
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
      limit 1)
  )
`;

const Link = v.object({ name: v.string(), path: v.nullable(v.string()) });

const PartyRef = v.object({
  id: v.string(),
  label: v.string(),
  short: v.nullable(v.string()),
  color: v.nullable(v.string()),
});

const ResultSet = v.object({
  stage: v.string(),
  status: v.string(),
  completeness: v.nullable(v.number()),
  isDefault: v.boolean(),
  source: v.object({
    kind: v.string(),
    publisher: v.string(),
    title: v.string(),
    url: v.nullable(v.string()),
    license: v.nullable(v.string()),
    attribution: v.nullable(v.string()),
  }),
});

const Cell = v.nullable(
  v.object({
    label: v.string(),
    person: v.nullable(v.string()),
    party: v.nullable(v.string()),
    votes: v.number(),
    share: v.number(),
    outcome: v.nullable(v.string()),
    seats: v.nullable(v.number()),
  }),
);

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
    rows: v.array(
      v.object({
        k: v.number(),
        at: v.optional(v.nullable(v.tuple([v.number(), v.number()]))),
        name: v.string(),
        path: v.nullable(v.string()),
        contest: v.boolean(),
        isRemainder: v.boolean(),
        valid: v.number(),
        shares: v.array(v.number()),
        leader: v.nullable(v.number()),
        margin: v.nullable(v.number()),
        earlier: v.nullable(v.array(v.number())),
        prior: v.nullable(v.number()),
      }),
    ),
    sources: v.array(v.string()),
  }),
);

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

async function names(ids: Array<string>, on: string | null = null) {
  const result = await db.execute<{
    entity_id: string;
    kind: string;
    text: string;
  }>(sql`
    select distinct on (entity_id, kind = 'abbreviation') entity_id, kind::text, text
    from name
    where entity_id = any(${sql.param(ids)}::uuid[]) and kind in ('official', 'english', 'abbreviation')
    order by entity_id, kind = 'abbreviation', coalesce(valid @> ${on}::date, false) desc, is_preferred desc,
      (lang = 'en') desc, (kind = 'official') desc
  `);
  const full = new Map<string, string>();
  const short = new Map<string, string>();
  for (const row of result.rows) {
    (row.kind === "abbreviation" ? short : full).set(row.entity_id, row.text);
  }
  return { full, short };
}

async function paths(ids: Array<string>) {
  const result = await db.execute<{ entity_id: string; path: string }>(sql`
    select entity_id, path from slug where is_current and entity_id = any(${sql.param(ids)}::uuid[])
  `);
  return new Map(result.rows.map((row) => [row.entity_id, row.path]));
}

async function breadcrumbs(polity: string) {
  const result = await db.execute<{ id: string }>(sql`
    with recursive chain(id, depth) as (
      select ${polity}::uuid, 0
      union all
      select p.parent_id, chain.depth + 1 from polity_parent p join chain on p.polity_id = chain.id where chain.depth < 8
    )
    select id from chain group by id order by max(depth) desc
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
    select c.id, c.kind::text as kind, case when r.kind = 'ballot_question' then r.id::text
      else concat_ws(':', r.office_id, r.body_id, r.kind, r.round_number, r.party_scope_id) end as series,
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
  contest: string,
  area: { id: string; k: number },
  isRoot: boolean,
  date: string,
) {
  const sets = await db.execute<{ k: number }>(sql`
    with recursive grp(id) as (
      select ${contest}::uuid
      union all
      select c.id from contest c join grp on c.allocation_parent_id = grp.id
    )
    select rs.k from result_set rs join grp on grp.id = rs.contest_id
  `);
  const ks = sets.rows.map((row) => row.k);
  if (ks.length === 0) {
    return [];
  }
  let found = await db.execute<{ id: string; k: number; scheme: string }>(sql`
    select a.id, a.k, a.scheme_code as scheme
    from area_parent p join area a on a.id = p.area_id
    where p.parent_id = ${area.id}::uuid and p.valid @> ${date}::date and exists (
      select 1 from result_rollup r
      where r.result_set_k = any(${sql.param(ks)}::int[]) and r.area_k = a.k and r.measure = any(${COUNTED})
    )
  `);
  if (isRoot && found.rows.length < 2) {
    found = await db.execute<{ id: string; k: number; scheme: string }>(sql`
      with recursive anc(id) as (
        select ${area.id}::uuid
        union
        select p.parent_id from area_parent p join anc on p.area_id = anc.id
      )
      select a.id, a.k, a.scheme_code as scheme
      from (
        select distinct area_k from result_rollup
        where result_set_k = any(${sql.param(ks)}::int[]) and measure = any(${COUNTED})
      ) r
      join area a on a.k = r.area_k
      where a.id not in (select id from anc)
    `);
  }
  if (found.rows.length === 0) {
    return [];
  }
  const [labels, schemes] = await Promise.all([
    names(
      found.rows.map((row) => row.id),
      date,
    ),
    db.execute<{ code: string; noun: string; kind: string; rank: number }>(sql`
      select code, noun, kind::text, rank from scheme
      where code = any(${sql.param([...new Set(found.rows.map((row) => row.scheme))])}::text[])
    `),
  ]);
  const scheme = new Map(schemes.rows.map((row) => [row.code, row]));
  const taken = new Map<string, number>();
  return found.rows
    .toSorted((a, b) => a.k - b.k)
    .map((row) => {
      const name = labels.full.get(row.id) ?? "";
      const base = segment(name);
      const seen = (taken.get(base) ?? 0) + 1;
      taken.set(base, seen);
      return {
        ...row,
        name,
        segment: seen === 1 ? base : `${base}-${seen}`,
        noun: scheme.get(row.scheme)?.noun ?? "",
        kind: scheme.get(row.scheme)?.kind ?? "",
        rank: scheme.get(row.scheme)?.rank ?? 0,
      };
    })
    .toSorted((a, b) =>
      a.name.localeCompare(b.name, undefined, { numeric: true }),
    );
}

async function descend(contest: Found, unit: string | undefined) {
  const chain: Awaited<ReturnType<typeof children>> = [];
  const pieces = (unit ?? "").split("/").filter((piece) => piece !== "");
  if (pieces.length > 0 && contest.place == null) {
    throw new ORPCError("NOT_FOUND");
  }
  let current = contest.place ?? { id: "", k: 0 };
  for (const piece of pieces) {
    const kids = await children(
      contest.id,
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
    const candidates = VARIANTS.filter(
      ([step]) => shown + step >= 0.52 && shown + step <= 0.86,
    ).map(([step, turn]) => {
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
        if (fitted.rgb.every((value) => value >= -0.0005 && value <= 1.0005)) {
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
              ...fitted.at.map((value, index) => value - (other[index] ?? 0)),
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
}

async function load(
  roots: Array<string>,
  areaKs: Array<number>,
  date: string,
  outcomes: Array<string>,
  owners: Array<string> = [],
) {
  const loaded = new Map<
    number,
    {
      side: Side;
      measure: string;
      appointed: number | null;
      exact: boolean;
      ks: Array<number>;
      sets: Array<v.InferOutput<typeof ResultSet>>;
    }
  >();
  if (areaKs.length === 0) {
    return loaded;
  }
  const picked = await db.execute<{
    area: number;
    k: number;
    contest: string;
    root: string;
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
  }>(sql`
    with recursive grp(id, root) as (
      select id, id from unnest(${sql.param(roots)}::uuid[]) as r(id)
      union all
      select c.id, grp.root from contest c join grp on c.allocation_parent_id = grp.id
    ),
    picked as (
      select distinct on (rs.contest_id, a.k) a.k as area, rs.k, rs.contest_id as contest, grp.root, rs.stage, rs.status,
        rs.completeness, rs.is_default, rs.source_id
      from result_set rs join grp on grp.id = rs.contest_id
      ${
        owners.length === 0
          ? sql`cross join unnest(${sql.param(areaKs)}::int[]) as a(k)`
          : sql`join unnest(${sql.param(areaKs)}::int[], ${sql.param(owners)}::uuid[]) as a(k, owner) on a.owner = grp.root`
      }
      join lateral (
        select 1 from result_rollup r where r.result_set_k = rs.k and r.area_k = a.k and r.measure = any(${COUNTED}) limit 1
      ) hit on true
      order by rs.contest_id, a.k, rs.is_default desc, array_position(${STAGES}, rs.stage),
        rs.completeness desc nulls last
    )
    select p.area, p.k, p.contest, p.root, p.stage::text, p.status::text, p.completeness::float8 as completeness, p.is_default,
      s.kind::text, s.publisher, s.title, s.url, s.license, s.attribution
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
    exact: boolean;
  }>(sql`
    with fetched as materialized (
      select r.result_set_k as set, r.area_k as area, r.option_k as option, r.measure, r.count_round,
        r.value::float8 as value, r.exact,
        min(r.count_round) over (partition by r.result_set_k, r.area_k, r.measure) as first
      from unnest(${sql.param(picked.rows.map((row) => row.k))}::int[], ${sql.param(picked.rows.map((row) => row.area))}::int[]) as p(set, area)
      join result_rollup r on r.result_set_k = p.set and r.area_k = p.area
      where (r.measure = any(${COUNTED}) or r.option_k = 0 and r.measure in (2, 4))
    ),
    mains as materialized (
      select area, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main from fetched group by area
    ),
    flagged as (
      select f.*, m.main, bool_or(f.measure = m.main) over (partition by f.set, f.area) as kept
      from fetched f join mains m on m.area = f.area
    )
    select r.set, r.area, r.option, x.role, r.main, r.value, r.exact
    from flagged r
    cross join lateral (
      select case
        when r.measure = r.main then 'votes'
        when r.measure = 4 and r.main = 1 then 'valid'
        when r.measure = 2 and r.main = 11 then 'appointed'
      end as role
    ) x
    where r.kept and r.count_round = r.first and x.role is not null
  `);
  const kept = new Set(rollup.rows.map((row) => `${row.set}:${row.area}`));
  const tallies = new Map<number, number>();
  const areas = new Map<
    number,
    {
      votes: Map<number, number>;
      sums: Map<string, number>;
      main: number;
      exact: boolean;
    }
  >();
  for (const row of rollup.rows) {
    const area = areas.get(row.area) ?? {
      votes: new Map<number, number>(),
      sums: new Map<string, number>(),
      main: row.main,
      exact: true,
    };
    areas.set(row.area, area);
    area.exact &&= row.exact;
    const sum = `${row.role}:${row.set}`;
    area.sums.set(sum, (area.sums.get(sum) ?? 0) + row.value);
    if (row.role === "votes") {
      area.votes.set(row.option, (area.votes.get(row.option) ?? 0) + row.value);
      tallies.set(row.option, (tallies.get(row.option) ?? 0) + row.value);
    }
  }
  const optionKs = new Set(tallies.keys());

  const options = await db.execute<{
    k: number;
    contest: string;
    kind: string;
    party: string | null;
    person: string | null;
    label: string | null;
    outcome: string | null;
    seats: number | null;
    alliance: string | null;
  }>(sql`
    select o.k, o.contest_id as contest, o.kind::text, coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label,
      o.alliance_id as alliance,
      (select coalesce(m.into_id, cy.person_id) from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
       where cy.option_id in (o.id, o.parent_option_id) and cy.role = 'head'
         and not exists (select from candidacy cz where cz.option_id = cy.option_id and cz.role = 'head' and cz.id <> cy.id)
       order by cy.option_id = o.id desc, cy.id limit 1) as person,
      oc.kind::text as outcome, oc.seats
    from contest_option o
    left join entity_merge pm on pm.entity_id = o.party_id
    left join lateral (
      select (array_agg(x.kind order by array_position(array['elected', 'advanced', 'won', 'tied']::outcome_kind[], x.kind)))[1] as kind,
        coalesce(max(x.seats), nullif(count(*) filter (where x.kind = 'elected'), 0))::int as seats
      from outcome x join result_set rs on rs.id = x.result_set_id
      where x.option_id = o.id and o.contest_id = any(${sql.param(outcomes)}::uuid[]) and rs.is_default
        and x.kind in ('elected', 'won', 'advanced', 'tied') and x.candidacy_id is null
    ) oc on true
    where (o.k = any(${sql.param([...optionKs])}::int[]) or o.contest_id = any(${sql.param(outcomes)}::uuid[]))
      and (o.k = any(${sql.param([...optionKs])}::int[]) or oc.kind is not null)
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
    db.execute<{ alliance: string; party: string }>(sql`
      select distinct on (m.alliance_id) m.alliance_id as alliance, coalesce(pm.into_id, m.party_id) as party
      from alliance_member m
      left join entity_merge pm on pm.entity_id = m.party_id
      left join contest_option o on o.party_id = m.party_id and o.alliance_id = m.alliance_id
      left join outcome x on x.option_id = o.id and x.kind in ('elected', 'won')
      where m.alliance_id = any(${sql.param([...new Set(options.rows.flatMap((row) => (row.party == null ? (row.alliance ?? []) : [])))])}::uuid[])
      group by m.alliance_id, coalesce(pm.into_id, m.party_id)
      order by m.alliance_id, count(x.*) desc
    `),
  ]);
  const anchor = new Map(
    coalitions.rows.map((row) => [row.alliance, row.party]),
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
  const [labels, colors, positions] = await Promise.all([
    names(entities, date),
    db.execute<{ party_id: string; color: string }>(sql`
      select p.id as party_id, c.color from unnest(${sql.param(parties)}::uuid[]) p(id),
        lateral (select ${paint(sql`p.id`, sql`select election_id from contest where id = any(${sql.param(roots)}::uuid[])`, sql`${date}::date`)} as color) c
      where c.color is not null
    `),
    db.execute<{ party_id: string; lean: number }>(sql`
      select party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
      where party_id = any(${sql.param(parties)}::uuid[]) and valid @> ${date}::date
    `),
  ]);
  const color = new Map(colors.rows.map((row) => [row.party_id, row.color]));
  const lean = new Map(positions.rows.map((row) => [row.party_id, row.lean]));
  const top = Math.max(0, ...tallies.values());
  const independents = options.rows.filter(
    (row) =>
      stand(row) == null &&
      row.person != null &&
      (tallies.get(row.k) ?? 0) >= top * 0.05,
  );
  for (const { person } of independents) {
    color.set(
      person ?? "",
      UNAFFILIATED[
        Number.parseInt((person ?? "").slice(-4), 16) % UNAFFILIATED.length
      ] ?? "",
    );
  }
  separate(
    color,
    options.rows.map(
      (row) => [stand(row) ?? row.person, tallies.get(row.k) ?? 0] as const,
    ),
  );
  const rootOf = new Map(picked.rows.map((row) => [row.contest, row.root]));
  const sharing = Map.groupBy(
    options.rows.filter((row) => row.person != null && row.party != null),
    (row) => `${rootOf.get(row.contest) ?? row.contest}:${row.party ?? ""}`,
  );
  const shared = new Set(sharing.values().map((rows) => rows[0]?.party ?? ""))
    .size;
  const tints = new Map(
    sharing.values().flatMap((rows) => {
      const base = color.get(rows[0]?.party ?? "");
      const order = [
        ...new Set(
          rows
            .toSorted(
              (a, b) => (tallies.get(b.k) ?? 0) - (tallies.get(a.k) ?? 0),
            )
            .map((row) => row.person),
        ),
      ];
      return rows.flatMap((row) => {
        const rank = order.indexOf(row.person);
        const step =
          (rank % 2 === 1 ? 1 : -1) * (0.06 + 0.08 * Math.ceil(rank / 2));
        if (base == null || rank < 1) {
          return [];
        }
        return [
          [
            row.k,
            shared === 1
              ? `oklch(from ${base} l max(c, 0.1) calc(h + ${rank * 137.5}))`
              : `oklch(from ${base} calc(l + ${step}) c h)`,
          ] as const,
        ];
      });
    }),
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
      total +=
        (isAnswers ? null : area.sums.get(`valid:${set.k}`)) ??
        area.sums.get(`votes:${set.k}`) ??
        0;
      appointed += area.sums.get(`appointed:${set.k}`) ?? 0;
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
            seats: row.seats,
            alliance:
              row.alliance == null
                ? null
                : {
                    id: row.alliance,
                    label: labels.full.get(row.alliance) ?? "",
                  },
          })),
        backing: backers,
      },
      measure: MEASURES.get(area.main) ?? "votes",
      appointed: appointed > 0 ? appointed : null,
      exact: area.exact,
      ks: sets.map((set) => set.k),
      sets: sets.map((row) => ({
        stage: row.stage,
        status: row.status,
        completeness: row.completeness,
        isDefault: row.is_default,
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
    from result_rollup r join result_set rs on rs.k = r.result_set_k
    where rs.contest_id = ${contest}::uuid and rs.is_default and r.measure = 16 and r.count_round = 0
      and r.area_k = any(${sql.param(kids.map((kid) => kid.k))}::int[])
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
    name: labels.full.get(option.area ?? option.contest) ?? "",
    path: option.path,
  };
  const result = chosen.map((entry) => ({
    place: own,
    name: labels.full.get(entry.person) ?? null,
    position: entry.position,
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
      result.push({ place, name: null, position: null });
    }
  }
  while (result.length < option.seats) {
    result.push({ place: own, name: null, position: null });
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
    seats: number;
    person: string | null;
    position: number | null;
    isSeated: boolean;
    isElected: boolean;
  }>(sql`
    select c.id as contest, c.district_area_id as area, s.path, o.k, o.kind::text as kind,
      coalesce(pm.into_id, lp.party) as party, coalesce(x.seats, 1) as seats,
      coalesce(m.into_id, y.person_id) as person, y.list_position as position,
      coalesce(y.seated, false) as "isSeated", coalesce(y.elected, false) as "isElected"
    from contest c
    left join slug s on s.entity_id = c.id and s.is_current
    join result_set rs on rs.contest_id = c.id and rs.is_default
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
          where s.election_id = c.election_id and s.body_id = c.body_id and s.allocation_parent_id is null
            and s.party_scope_id is not distinct from c.party_scope_id and s.kind in ('body_seats', 'list_allocation')
        )
      end as roots,
      a.k as nation, a.id as area,
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
  const [loaded, named, current] = await Promise.all([
    shape?.nation == null || shape.roots.length === 0
      ? null
      : load(
          shape.roots,
          [shape.nation],
          contest.date ?? "infinity",
          shape.roots,
        ),
    names(
      [shape?.area].filter((id) => id != null),
      contest.date,
    ),
    now,
  ]);
  const seated = (entry: { side: Side } | undefined) =>
    entry == null
      ? []
      : compare(entry.side, null, lineage, "party").filter(
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
  const { places, seats } = await holders(
    isAlone ? [contest.id] : shape.roots,
    contest.date,
    lineage,
    new Set(rows.map((row) => row.key)),
    isAlone && shape?.isParent !== true
      ? await units(
          contest.id,
          area,
          contest.date ?? "infinity",
          shape?.base ?? null,
        )
      : null,
  );
  const at = places.findIndex((place) => place.id === contest.id);
  return {
    link: isFocus
      ? {
          name: named.full.get(shape?.area ?? "") ?? "",
          path: shape?.path ?? null,
        }
      : null,
    size: shape?.size ?? null,
    places: places.map((place) => ({ name: place.name, path: place.path })),
    here: at === -1 ? null : at,
    parties: rows.map((row) => ({
      key: row.key,
      label: row.label,
      short: row.short,
      color: here.get(row.key)?.color ?? row.color,
      lean: row.lean,
      seats: row.current?.seats ?? 0,
      here: here.get(row.key)?.current?.seats ?? 0,
      members: seats.get(row.key) ?? [],
    })),
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
): Promise<v.InferOutput<typeof Places>> {
  const kids = await children(
    contest.id,
    area,
    isRoot,
    contest.date ?? "infinity",
  );
  const counts = new Map<
    string,
    { code: string; noun: string; kind: string; rank: number; count: number }
  >();
  for (const kid of kids) {
    const level = counts.get(kid.scheme) ?? {
      code: kid.scheme,
      noun: kid.noun,
      kind: kid.kind,
      rank: kid.rank,
      count: 0,
    };
    level.count += 1;
    counts.set(kid.scheme, level);
  }
  const eligible = counts
    .values()
    .filter((level) => level.count > 1)
    .toArray();
  if (eligible.length === 0) {
    return null;
  }
  const allocated = await db.execute<{ area: string; path: string | null }>(sql`
    select c.district_area_id as area, s.path
    from contest c left join slug s on s.entity_id = c.id and s.is_current
    where c.allocation_parent_id = ${contest.id}::uuid and c.district_area_id is not null
  `);
  const childPath = new Map(allocated.rows.map((row) => [row.area, row.path]));
  const rank = Math.min(...eligible.map((level) => level.rank));
  const levels = eligible
    .filter((level) => level.rank === rank)
    .toSorted(
      (a, b) =>
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
  const level = levels.find((entry) => entry.code === by) ?? levels[0];
  if (level == null) {
    return null;
  }
  const rows = kids
    .filter((kid) => kid.scheme === level.code)
    .map((kid) => {
      const child = childPath.get(kid.id);
      return {
        k: kid.k,
        name: kid.name,
        path: child ?? (base == null ? null : `${base}/${kid.segment}`),
        isContest: child != null,
        owner: null,
      };
    });
  const [tabulated, points] = await Promise.all([
    tabulate(
      [contest.root],
      rows,
      contest.date,
      chosen,
      lineage,
      match,
      grouping,
    ),
    db.execute<{ k: number; x: number; y: number }>(sql`
      select distinct on (a.k) a.k,
        ST_X(coalesce(g.label_point, ST_PointOnSurface(g.geom))) as x,
        ST_Y(coalesce(g.label_point, ST_PointOnSurface(g.geom))) as y
      from area a join area_geometry g on g.area_id = a.id
      where a.k = any(${sql.param(rows.map((row) => row.k))}::int[])
        and g.valid @> ${contest.date ?? "infinity"}::date
      order by a.k
    `),
  ]);
  const at = new Map(
    points.rows.map((row) => [row.k, [row.x, row.y] as [number, number]]),
  );
  return {
    levels: levels.map((entry) => ({
      code: entry.code,
      noun: entry.noun,
      count: entry.count,
    })),
    level: level.code,
    ...tabulated,
    rows: tabulated.rows.map((row) => ({ ...row, at: at.get(row.k) ?? null })),
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
    owner: string | null;
  }>,
  date: string | null,
  chosen: { id: string; date: string | null } | null,
  lineage: Array<Relation>,
  match: Match,
  grouping: Grouping = "option",
) {
  const ks = rows.map((row) => row.k);
  const depth = new Map<number, number>();
  const tier = rows.map((row) => {
    const n = depth.get(row.k) ?? 0;
    depth.set(row.k, n + 1);
    return n;
  });
  const [now, then] = await Promise.all([
    Promise.all(
      Array.from({ length: Math.max(0, ...tier) + 1 }, async (_, n) => {
        const batch = rows.filter((_row, index) => tier[index] === n);
        return load(
          roots,
          batch.map((row) => row.k),
          date ?? "infinity",
          [],
          batch.flatMap((row) => (row.owner == null ? [] : [row.owner])),
        );
      }),
    ),
    chosen == null
      ? null
      : load([chosen.id], ks, chosen.date ?? "infinity", []),
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
  const compared = rows.map((row, index) => {
    const current = now[tier[index] ?? 0]?.get(row.k);
    const sets = current?.sets ?? [];
    for (const set of sets) {
      sources.add(set.source.publisher);
    }
    const result =
      current == null
        ? []
        : compare(
            current.side,
            then?.get(row.k)?.side ?? null,
            lineage,
            match,
            grouping,
          );
    const running = result.find(
      (entry) => entry.current != null && !entry.key.startsWith("kind:"),
    );
    const leader = running ?? null;
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
        short: entry.short,
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
        (entry.key === before?.key && before.key !== leader?.key);
      legend.set(entry.key, item);
    }
    return { row, result, leader, before, valid: current?.side.valid ?? 0 };
  });
  const ranked = legend
    .values()
    .filter((item) => item.votes > 0 || item.lead)
    .toArray()
    .toSorted((a, b) => b.votes - a.votes);
  const kept = ranked.filter((item, index) => index < 8 || item.lead);
  const position = new Map(kept.map((item, index) => [item.key, index]));
  return {
    legend: kept.map((item) => ({
      key: item.key,
      label: item.label,
      short: item.key.startsWith("person:") ? null : item.short,
      color: item.color,
      person:
        item.key.startsWith("person:") || item.people.size === 1
          ? item.person
          : null,
      party: item.party,
    })),
    rows: compared.map(({ row, result, leader, before, valid }) => {
      const runner = result.find(
        (entry) =>
          entry.current != null &&
          !entry.key.startsWith("kind:") &&
          entry.key !== leader?.key,
      );
      return {
        k: row.k,
        name: row.name,
        path: row.path,
        contest: row.isContest,
        isRemainder: row.isRemainder ?? false,
        valid,
        shares: kept.map(
          (item) =>
            result.find((entry) => entry.key === item.key)?.current?.share ?? 0,
        ),
        leader: leader == null ? null : (position.get(leader.key) ?? null),
        margin:
          leader?.current == null
            ? null
            : leader.current.share - (runner?.current?.share ?? 0),
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
    }),
    sources: [...sources],
  };
}

type Note = {
  code:
    | "no_target"
    | "not_covered"
    | "different_race"
    | "annulled"
    | "partial"
    | "alternative";
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
  contest: { series: string; date: string | null },
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
  group: v.pipe(
    v.optional(v.string(), "option"),
    v.transform((value): Grouping =>
      value === "alliance" ? "alliance" : "option",
    ),
  ),
});

export const contests = {
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
          trail: v.array(
            v.object({
              name: v.string(),
              path: v.nullable(v.string()),
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
              }),
            ),
          }),
        ),
        notes: v.array(v.object({ kind: v.string(), body: v.string() })),
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
              }),
            ),
          }),
        ),
        current: v.nullable(
          v.object({
            valid: v.number(),
            measure: v.string(),
            exact: v.boolean(),
            sets: v.array(ResultSet),
            isAllied: v.boolean(),
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
                  }),
                ),
              }),
            ),
          }),
        ),
        target: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            year: v.nullable(v.number()),
            status: v.string(),
            valid: v.number(),
            exact: v.boolean(),
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
      }),
    )
    .handler(async ({ input }) => {
      const contest = await find(input.id);
      const chain = await descend(contest, input.unit);
      const scoped = chain.at(-1);
      const isRoot = scoped == null;
      const area = scoped ?? contest.place;
      const unit = chain.map((entry) => entry.segment);
      const [parts, ancestors, cited, home] = await Promise.all([
        db.execute<{ id: string }>(sql`
          select id from contest where allocation_parent_id = ${contest.id}::uuid
        `),
        db.execute<{
          id: string;
          area: string | null;
          scheme: string | null;
          noun: string | null;
        }>(sql`
          with recursive up(id, parent, depth) as (
            select id, allocation_parent_id, 0 from contest where id = ${contest.id}::uuid
            union all
            select c.id, c.allocation_parent_id, up.depth + 1 from contest c join up on c.id = up.parent
          )
          select up.id, a.id as area, a.scheme_code as scheme, sc.noun
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
          from citation ct join source s on s.id = ct.source_id
          where ct.entity_id = ${contest.id}::uuid or ct.entity_id in (
            select cy.id from candidacy cy join contest_option o on o.id = cy.option_id
            where o.contest_id = ${contest.id}::uuid
          )
          order by s.publisher, s.title
        `),
        db.execute<{ path: string }>(sql`
          select s.path from polity_territory t join slug s on s.entity_id = t.polity_id and s.is_current
          where t.area_id = ${area?.id}::uuid and t.polity_id <> ${contest.polity}::uuid
          order by upper(t.valid) desc nulls first limit 1
        `),
      ]);
      const childIds = parts.rows.map((row) => row.id);
      const ancestorIds = ancestors.rows.map((row) => row.id);
      const [labels, slugs, crumbs, notes, sets, declared] = await Promise.all([
        names(
          [
            contest.id,
            contest.election,
            ...childIds,
            ...ancestors.rows.map((row) => row.area).filter((id) => id != null),
          ],
          contest.date,
        ),
        paths([contest.id, contest.election, ...childIds, ...ancestorIds]),
        breadcrumbs(contest.polity),
        db.execute<{ kind: string; body: string }>(sql`
          select kind::text, body_md as body from note
          where entity_id in (${contest.id}::uuid, ${contest.election}::uuid)
            or entity_id in (select id from result_set where contest_id = ${contest.id}::uuid)
            or entity_id in (
              select cy.id from candidacy cy join contest_option o on o.id = cy.option_id
              where o.contest_id = ${contest.id}::uuid
            )
          order by array_position(array['caveat','context','methodology','editorial']::note_kind[], kind)
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
        }>(sql`
          select rs.stage::text, rs.status::text, rs.completeness::float8 as completeness, rs.is_default,
            s.kind::text, s.publisher, s.title, s.url, s.license, s.attribution
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
        }>(sql`
           select o.id as key, rs.kind::text, coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label,
             (select coalesce(m.into_id, cy.person_id) from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
              where cy.option_id in (o.id, o.parent_option_id) and cy.role = 'head'
          and not exists (select from candidacy cz where cz.option_id = cy.option_id and cz.role = 'head' and cz.id <> cy.id)
              order by cy.option_id = o.id desc, cy.id limit 1) as person,
             x.kind::text as outcome
           from result_set rs
           join outcome x on x.result_set_id = rs.id and x.candidacy_id is null
           join contest_option o on o.id = x.option_id
           left join entity_merge pm on pm.entity_id = o.party_id
           where ${isRoot}::boolean and rs.contest_id = ${contest.id}::uuid and rs.is_default and rs.kind <> 'counted'
           order by o.k
        `),
      ]);
      const declaredParties = [
        ...new Set(
          declared.rows.map((row) => row.party).filter((id) => id != null),
        ),
      ];
      const [declaredLabels, declaredColors] = await Promise.all([
        names(
          [
            ...declaredParties,
            ...declared.rows
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
      const path = slugs.get(contest.id) ?? null;
      const scopePath = path == null ? null : [path, ...unit].join("/");
      const trail = [
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
          trail,
        },
        series: [],
        notes: notes.rows,
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
                })),
              },
        current: null,
        electors: null,
        chamber: null,
        target: null,
        comparison: { label: null, notes: [] },
        rows: [],
        targets: [],
      };
      if (area == null) {
        return page;
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
      }>(sql`
        with recursive anc(id) as (
          select ${area.id}::uuid
          union
          select p.parent_id from area_parent p join anc on p.area_id = anc.id
        ),
        roots as (
          select c.id, case when c.kind = 'ballot_question' then c.id::text
            else concat_ws(':', c.office_id, c.body_id, c.kind, c.round_number, c.party_scope_id) end as series,
            e.id as election, e.kind = 'indirect' as indirect, e.date_from, ${CYCLE} as year, e.status,
            c.term is not distinct from 'remainder' as "isRemainder"
          from contest c
          join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          where c.allocation_parent_id is null and coalesce(c.district_area_id, pt.area_id) in (select id from anc)
        ),
        grp(root, id) as (
          select id, id from roots
          union all
          select grp.root, c.id from contest c join grp on c.allocation_parent_id = grp.id
        )
        select r.id, r.series, r.election, r.indirect, r.date_from::text as date, r.year, r.status::text as status,
          r."isRemainder"
        from roots r
        where r.id <> ${contest.root}::uuid and exists (
          select 1 from grp join result_set rs on rs.contest_id = grp.id
          where grp.root = r.id and exists (
            select 1 from result_rollup x where x.result_set_k = rs.k and x.area_k = ${area.k} and x.measure = any(${COUNTED})
          )
        )
        order by r.date_from desc nulls last, r.id
      `);
      const rootIds = covering.rows.map((row) => row.id);
      const sameRace = covering.rows.filter(
        (row) => row.series === contest.series,
      );
      const comparable = covering.rows.filter(
        (row) => row.indirect === contest.indirect,
      );
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
          join contest c on c.id = grp.id
          join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join anc on anc.id = coalesce(c.district_area_id, pt.area_id)
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
             select id, id from contest where id = any(${sql.param([...sameRace.map((row) => row.id), contest.root])}::uuid[])
             union all
             select grp.root, c.id from contest c join grp on c.allocation_parent_id = grp.id
           ),
           sets as materialized (
             select grp.root, rs.k, rs.contest_id, rs.is_default, rs.stage, rs.completeness
             from result_set rs join grp on grp.id = rs.contest_id
           ),
           picked as (
             select distinct on (rs.contest_id) rs.root, rs.k
             from sets rs
             join lateral (
               select 1 from result_rollup r where r.result_set_k = rs.k and r.area_k = ${area.k} and r.measure = any(${COUNTED}) limit 1
             ) hit on true
             order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
           ),
           fetched as (
             select p.root, r.*
             from picked p cross join lateral (
               select r.option_k as option, r.measure, r.value::float8 as value, r.count_round,
                 min(r.count_round) over (partition by r.measure) as first
               from result_rollup r
               where r.result_set_k = p.k and r.area_k = ${area.k}
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
             where f.count_round = f.first
           ),
           grouped as (
             select root, key, min(main) as measure, min(appointed) as appointed, (array_agg(person))[1] as person,
               (array_agg(party order by party is null, value desc))[1] as party, min(kind) as kind, min(label) as label,
               sum(value) as votes, sum(sum(value)) over (partition by root) as total
             from tallied group by root, key
           ),
           tops as (
             select distinct on (root) * from grouped order by root, votes desc
           )
           select t.root, t.person, t.label, t.kind, t.measure, t.votes, case when t.measure = 11 then coalesce(t.appointed, t.total) else t.total end as total, x.party,
             ${paint(sql`x.party`, sql`e.id`, sql`e.date_from`)} as color,
             (select n.text from name n where n.entity_id = x.party and n.kind in ('official', 'english')
               order by coalesce(n.valid @> e.date_from, false) desc, n.is_preferred desc, (n.lang = 'en') desc,
                 (n.kind = 'official') desc limit 1) as party_label,
             (select n.text from name n where n.entity_id = x.party and n.kind = 'abbreviation'
               order by coalesce(n.valid @> e.date_from, false) desc, n.is_preferred desc, (n.lang = 'en') desc
               limit 1) as party_short
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
      const leaders = await names(
        tops.rows.flatMap((row) => (row.person == null ? [] : [row.person])),
      );
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
          const spot = located.rows.find((entry) => entry.root === row.id);
          const base =
            spot == null
              ? (rootSlugs.get(row.id) ?? null)
              : (locatedSlugs.get(spot.contest) ?? null);
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
            a.name.localeCompare(b.name, undefined, { numeric: true }),
        )
        .map((entry) => ({
          ...entry,
          winner:
            entries.find(
              (peer) =>
                !entry.indirect && peer.indirect && peer.year === entry.year,
            )?.winner ?? entry.winner,
        }));
      const [{ chosen, notes: picked }, { chosen: previous }] =
        await Promise.all([
          pick(comparable, contest, input.vs),
          pick(comparable, contest, "previous"),
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
      );
      const [now, before, grid, cast, seats] = await Promise.all([
        loading,
        chosen == null
          ? null
          : load(
              [chosen.id],
              [area.k],
              chosen.date ?? "infinity",
              [chosen.id].filter(() => isRoot),
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
        ),
        counterpart?.indirect === true
          ? load([counterpart.id], [area.k], counterpart.date ?? "infinity", [])
          : null,
        chamber(contest, area, loading, lineage, isRoot),
      ]);
      const current = now.get(area.k);
      if (current == null) {
        return { ...page, series, places: grid, targets };
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
        ...caveats("current", current.sets),
        ...caveats("target", earlier?.sets ?? []),
      ];
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
              exact: earlier.exact,
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
          votes: row.votes,
        }))
        .toSorted((a, b) => b.votes - a.votes);
      return {
        ...page,
        series,
        places: grid,
        checks,
        current: {
          valid: current.side.valid,
          measure: current.measure,
          exact: current.exact,
          sets: current.sets,
          isAllied: current.side.options.some(
            (option) => option.alliance != null && option.votes > 0,
          ),
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
        chamber: seats,
        target,
        comparison: {
          label:
            target == null
              ? null
              : current.exact && target.exact
                ? "exact"
                : "estimated",
          notes: notesOut,
        },
        rows,
        targets,
      };
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
          isApproximate: v.boolean(),
          sources: v.array(
            v.object({
              publisher: v.string(),
              title: v.string(),
              url: v.nullable(v.string()),
            }),
          ),
          count: v.number(),
          missing: v.array(v.string()),
          k: v.nullable(v.number()),
          frames: v.array(
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
        db.execute<{ scheme: string }>(sql`
          select scheme_code as scheme from area where id = ${area.id}::uuid
        `),
        input.level == null
          ? []
          : children(contest.id, area, chain.length === 0, day),
      ]);
      const scheme = own.rows[0]?.scheme ?? "";
      const groups = [
        { scheme, ids: [area.id] },
        ...(input.level == null || input.level === scheme
          ? []
          : [
              {
                scheme: input.level,
                ids: kids
                  .filter((kid) => kid.scheme === input.level)
                  .map((kid) => kid.id),
              },
            ]),
      ].filter((group) => group.ids.length > 0);
      const frames = await framing(area.id, day);
      const drawn = await Promise.all(
        groups.map(async (group) => {
          const found = await db.execute<{
            noun: string;
            vintage: string | null;
            asOf: string | null;
            tiles: string | null;
            shaped: Array<string>;
            qualities: Array<string>;
            sources: Array<{
              publisher: string;
              title: string;
              url: string | null;
            }> | null;
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
              (select json_agg(json_build_object('publisher', s.publisher, 'title', s.title, 'url', s.url) order by s.publisher)
               from source s where s.id in (select source_id from shapes)) as sources
            from scheme sc left join chosen on true
            where sc.code = ${group.scheme}
          `);
          const row = found.rows[0];
          if (row == null || (row.vintage == null && row.shaped.length === 0)) {
            return [];
          }
          const shaped = new Set(row.shaped);
          const absent = group.ids.filter((id) => !shaped.has(id));
          const labels = await names(absent, contest.date);
          return [
            {
              scheme: group.scheme,
              noun: row.noun,
              vintage: row.vintage,
              asOf: row.asOf,
              tiles: row.tiles,
              isApproximate: row.qualities.includes("synthetic"),
              sources: row.sources ?? [],
              count: group.ids.length,
              missing: absent
                .map((id) => labels.full.get(id) ?? "")
                .toSorted((a, b) => a.localeCompare(b)),
              k: group.ids.includes(area.id) ? area.k : null,
              frames: group.ids.includes(area.id) ? frames : [],
            },
          ];
        }),
      );
      return drawn.flat();
    }),
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
      return Promise.all(
        found.rows.map(async (row) => {
          const all =
            area == null
              ? null
              : await load([row.id], [area.k], row.date ?? "infinity", [
                  row.id,
                ]);
          const loaded = area == null ? undefined : all?.get(area.k);
          const leaders =
            loaded == null
              ? []
              : compare(loaded.side, null, [], "party")
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
                  .toSorted((a, b) => b.votes - a.votes)
                  .slice(0, 2);
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
            leaders,
          };
        }),
      );
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
          where c.id not in (select id from own)
        ),
        grp(id, top) as (
          select id, id from candidates
          union all
          select g.id, grp.top from contest g join grp on g.allocation_parent_id = grp.id
        ),
        hits as (
          select distinct grp.top from grp
          join result_set rs on rs.contest_id = grp.id
          where exists (
            select 1 from result_rollup r
            where r.result_set_k = rs.k and r.area_k = ${area.k} and r.measure = any(${COUNTED})
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
              : await children(row.id, other.place, true, day);
          const unit =
            other.place.k === area.k
              ? ""
              : kids.find((kid) => kid.k === area.k)?.segment;
          if (unit == null) {
            return null;
          }
          const totals = await load([row.id], [area.k], day, [row.id]);
          const loaded = totals.get(area.k);
          return {
            id: row.id,
            name: labels.full.get(row.id) ?? "",
            path: [row.path, unit].filter((part) => part !== "").join("/"),
            leaders:
              loaded == null
                ? []
                : compare(loaded.side, null, [], "party")
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
  lists: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}/lists",
        summary:
          "List a closed-list contest's ranked party lists, with each member's declared fate, the independents, and later list replacements",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          seats: v.number(),
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
                  position: v.number(),
                  person: v.string(),
                  name: v.string(),
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
      const found = await db.execute<{
        k: number;
        kind: string;
        party: string | null;
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
        select o.k, o.kind::text as kind, coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label,
          case when o.party_id is not null then
            ${paint(sql`coalesce(pm.into_id, o.party_id)`, sql`${contest.election}::uuid`, sql`${day}::date`)}
          end as color,
          (select max(x.seats) from outcome x where x.result_set_id = rs.id and x.option_id = o.id and x.candidacy_id is null)::int as seats,
          cy.id as candidacy, coalesce(m.into_id, cy.person_id) as person, cy.role::text as role, cy.list_position as position,
          f.kind::text as fate, f.notes as note
        from contest_option o
        join candidacy cy on cy.option_id = o.id
          and ((o.kind = 'list' and cy.role = 'list_member' and cy.list_position is not null) or (o.kind = 'others' and cy.role = 'head'))
        left join entity_merge m on m.entity_id = cy.person_id
        left join entity_merge pm on pm.entity_id = o.party_id
        left join lateral (
          select rs.id from result_set rs
          where rs.contest_id = o.contest_id and rs.is_default
          order by exists (select from outcome x where x.result_set_id = rs.id) desc, array_position(${STAGES}, rs.stage)
          limit 1
        ) rs on true
        left join lateral (
          select x.kind, x.notes from outcome x
          where x.result_set_id = rs.id and x.candidacy_id = cy.id and x.kind in ('elected', 'withdrew', 'disqualified')
          order by x.kind = 'elected' desc limit 1
        ) f on true
        where o.contest_id = ${contest.id}::uuid
      `);
      if (found.rows.every((row) => row.kind !== "list")) {
        return null;
      }
      const people = [...new Set(found.rows.map((row) => row.person))];
      const [votes, elsewhere, replaced] = await Promise.all([
        db.execute<{ k: number; votes: number }>(sql`
          with chosen as (
            select rs.k from result_set rs
            where rs.contest_id = ${contest.id}::uuid and rs.is_default and exists (
              select from result_rollup r where r.result_set_k = rs.k and r.area_k = ${contest.place?.k ?? 0} and r.measure = any(${COUNTED})
            )
            order by array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
            limit 1
          ),
          picked as (
            select r.option_k, r.measure, r.count_round, r.value
            from chosen
            join result_rollup r on r.result_set_k = chosen.k and r.area_k = ${contest.place?.k ?? 0}
            where r.measure = any(${COUNTED})
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
          join result_set rs on rs.contest_id = c.id and rs.is_default
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
              row.party == null ? [] : [row.party],
            ),
          ],
          contest.date,
        ),
        paths(seated.values().toArray()),
      ]);
      const tallies = new Map(votes.rows.map((row) => [row.k, row.votes]));
      const lists = Map.groupBy(
        found.rows.filter((row) => row.kind === "list"),
        (row) => row.k,
      )
        .entries()
        .map(([k, members]) => {
          const head = members[0];
          const party = head?.party ?? null;
          return {
            key: String(k),
            label:
              labels.full.get(party ?? "") ??
              head?.label ??
              labels.short.get(party ?? "") ??
              "",
            short: labels.short.get(party ?? "") ?? null,
            color: head?.color ?? null,
            party,
            votes: tallies.get(k) ?? null,
            seats:
              head?.seats ??
              members.filter((row) => row.fate === "elected").length,
            members: members
              .toSorted((a, b) => (a.position ?? 0) - (b.position ?? 0))
              .map((row) => {
                const other =
                  row.fate === "elected" ? null : seated.get(row.person);
                return {
                  position: row.position ?? 0,
                  person: row.person,
                  name: labels.full.get(row.person) ?? "",
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
        .filter((row) => row.kind === "others")
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

async function framing(area: string, day: string) {
  const parts = await db.execute<{
    west: number;
    south: number;
    east: number;
    north: number;
    share: number;
  }>(sql`
    with parts as (
      select (ST_Dump(ST_SimplifyPreserveTopology(g.geom, 0.02))).geom as geom
      from area_geometry g
      where g.area_id = ${area}::uuid and g.valid @> ${day}::date
    ),
    clustered as (
      select geom, ST_ClusterDBSCAN(ST_Envelope(geom), eps := 2, minpoints := 1) over () as cluster
      from parts
    ),
    grouped as (
      select ST_Extent(geom) as box, sum(ST_Area(geom) * cos(radians(ST_Y(ST_Centroid(geom))))) as size
      from clustered group by cluster
    )
    select ST_XMin(box) as west, ST_YMin(box) as south, ST_XMax(box) as east, ST_YMax(box) as north,
      size / nullif(sum(size) over (), 0) as share
    from grouped order by size desc
  `);
  return parts.rows
    .filter((part) => part.share >= 0.0005)
    .slice(0, 6)
    .map((part) => ({
      west: part.west,
      south: part.south,
      east: part.east,
      north: part.north,
    }));
}

async function sketch(
  area: { id: string; k: number },
  level: string,
  day: string,
) {
  const [frames, found] = await Promise.all([
    framing(area.id, day),
    db.execute<{ scheme: string; tiles: string; synthetic: boolean }>(sql`
      select distinct on (bs.scheme_code) bs.scheme_code as scheme,
        coalesce(bs.tiles_key, bs.scheme_code || '/' || bs.vintage) as tiles,
        exists (
          select from area_geometry g join area x on x.id = g.area_id
          where x.scheme_code = bs.scheme_code and g.quality = 'synthetic' and g.valid @> coalesce(bs.as_of, ${day}::date)
          limit 1
        ) as synthetic
      from boundary_set bs
      where bs.scheme_code in (${level}, (select scheme_code from area where id = ${area.id}::uuid))
        and bs.valid @> ${day}::date
      order by bs.scheme_code, bs.valid_from desc nulls last
    `),
  ]);
  const tiled = found.rows.find((row) => row.scheme === level);
  const own = found.rows.find((row) => row.scheme !== level);
  return tiled == null || frames.length === 0
    ? null
    : {
        tiles: tiled.tiles,
        own: own == null ? null : { tiles: own.tiles, k: area.k },
        frames,
        isApproximate: tiled.synthetic,
      };
}

async function races(
  election: string,
  polity: string,
  date: string | null,
  isBrief = false,
) {
  const day = date ?? "infinity";
  const [found, territory] = await Promise.all([
    db.execute<{
      id: string;
      kind: string;
      series: string;
      owner: string | null;
      office: string | null;
      election: string;
      day: string | null;
      round: number | null;
      isOffice: boolean;
      isStage: boolean;
      size: number | null;
      isCounted: boolean;
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
      runs as (
        select n.previous_round_id as id, array_agg(n.id) as ids
        from contest n join election ne on ne.id = n.election_id
        where ne.kind = 'runoff' and n.previous_round_id is not null
        group by n.previous_round_id
      )
      select c.id, c.kind::text as kind,
        coalesce(
          (select b.polity_id from body b where b.id = c.body_id),
          (select o.polity_id from office o where o.id = c.office_id),
          e.polity_id
        ) in (select id from anc) as "isOwn",
        case when c.kind = 'ballot_question' then c.id::text
          else concat_ws(':', c.office_id, c.body_id, c.kind, case when e.kind <> 'general' then e.date_from end, c.party_scope_id, tree.stage) end as series,
        coalesce(c.body_id, c.office_id) as owner, c.office_id as office, c.election_id as election,
        e.date_from::text as day, c.round_number as round,
        c.kind = 'office' as "isOffice", tree.stage as "isStage", z.seats as size,
        c.term is distinct from 'remainder' and coalesce(st.voting, true) as "isCounted",
        e.kind = 'runoff' as "isRunoff", c.previous_round_id as previous, c.party_scope_id as party,
        coalesce(runs.ids, '{}') as runoffs,
        c.term is not distinct from 'remainder' as "isRemainder",
        a.id as area, a.k, a.scheme_code as scheme, sc.noun,
        case when exists (
          select from contest_option o where o.contest_id = c.id and o.party_id is null and o.alliance_id is not null
        ) then 'alliance' else 'party' end as grouping
      from contest c
      join election e on e.id = c.election_id
      left join seat st on st.id = c.seat_id
      left join area a on a.id = c.district_area_id
      left join scheme sc on sc.code = a.scheme_code
      left join lateral (
        select z.seats from body_size z
        where z.body_id = c.body_id and (z.valid @> e.date_from or z.valid_from between e.date_from and e.date_from + 150)
        order by z.valid_from desc nulls last limit 1
      ) z on true
      join tree on tree.id = c.election_id
      left join runs on runs.id = c.id
      where c.allocation_parent_id is null or not exists (
        select from contest p join tree pt on pt.id = p.election_id where p.id = c.allocation_parent_id
      )
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
  const groups = Map.groupBy(owned, (row) => row.series)
    .values()
    .flatMap((group) =>
      group.length > 1 && new Set(group.map((row) => row.area)).size === 1
        ? group.map((row) => [row])
        : [group],
    )
    .toArray();
  if (nation == null || groups.length === 0) {
    return [];
  }
  const [labels, slugs, branches] = await Promise.all([
    names(
      found.rows.flatMap((row) => [
        row.id,
        ...[row.owner, row.area].filter((id) => id != null),
      ]),
      date,
    ),
    paths(found.rows.map((row) => row.id)),
    db.execute<{ from: string; to: string; kind: string }>(sql`
      select coalesce(fm.into_id, a.party_id) as from, coalesce(tm.into_id, a.parent_party_id) as to, a.kind::text
      from party_affiliation a
      left join entity_merge fm on fm.entity_id = a.party_id
      left join entity_merge tm on tm.entity_id = a.parent_party_id
      where a.kind = 'regional_branch' and a.valid @> ${day}::date
    `),
  ]);
  const lineage = branches.rows.map((row) => ({ ...row, toLabel: "" }));
  const parentOf = new Map(branches.rows.map((row) => [row.from, row.to]));
  const built = await Promise.all(
    groups.map(async (group) => {
      const roots = group.map((row) => row.id);
      const head = group[0];
      const single =
        head != null && group.length === 1 ? await find(head.id) : null;
      const place = single?.place ?? nation;
      const districts = group.filter((row) => row.k != null);
      const first = districts[0];
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
      const [totals, grid, table, spread] = await Promise.all([
        load(roots, [place.k], day, counted.length > 0 ? counted : roots),
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
          : tabulate(
              roots,
              districts.map((row) => ({
                k: row.k ?? 0,
                name: labels.full.get(row.area ?? "") ?? "",
                path: slugs.get(row.id) ?? null,
                isContest: true,
                isRemainder: row.isRemainder,
                owner: row.id,
              })),
              date,
              null,
              lineage,
              "party",
              grouping,
            ),
        db.execute<{ from: string; to: string }>(sql`
          with recursive tree(id) as (
            select unnest(${sql.param(roots)}::uuid[])
            union all
            select c.id from contest c join tree on c.allocation_parent_id = tree.id
          ),
          lines as materialized (
            select t.id as contest, o.id as option,
              coalesce(bm.into_id, b.parent_party_id, pm.into_id, o.party_id) as party
            from tree t
            join contest_option o on o.contest_id = t.id and o.party_id is not null
            left join entity_merge pm on pm.entity_id = o.party_id
            left join party_affiliation b on b.party_id = coalesce(pm.into_id, o.party_id)
              and b.kind = 'regional_branch' and b.valid @> ${day}::date
            left join entity_merge bm on bm.entity_id = b.parent_party_id
            where not exists (select from contest k where k.allocation_parent_id = t.id)
          ),
          reach as (
            select l.party, array_agg(distinct l.contest) as contests, count(distinct l.contest) as n
            from lines l
            where exists (
              select from lines w
              join outcome x on x.option_id = w.option and x.kind in ('elected', 'won')
              join result_set rs on rs.id = x.result_set_id and rs.is_default
              where w.party = l.party
            )
            group by l.party
          )
          select 'party:' || a.party as from, 'party:' || b.party as to
          from reach a join reach b on a.party < b.party
          where a.n + b.n = (select count(distinct contest) from lines)
            and not a.contests && b.contests
        `),
      ]);
      const here = totals.get(place.k);
      const rows = (
        here == null ? [] : compare(here.side, null, lineage, "party", grouping)
      ).flatMap((row) =>
        row.current == null ? [] : [{ ...row, cell: row.current }],
      );
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
      const results = folded
        .entries()
        .map(([key, members]) => {
          const party = key.startsWith("party:") ? key.slice(6) : null;
          const lead =
            members.find((row) => row.key === key) ??
            members.find((row) => row.cell.party === party) ??
            members[0];
          let [votes, share, seats] = [0, 0, null as number | null];
          for (const row of members) {
            votes += row.cell.votes;
            share += row.cell.share;
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
            color: lead?.color ?? null,
            lean: lead?.lean ?? null,
            person: members.length === 1 ? (lead?.cell.person ?? null) : null,
            party: party ?? lead?.cell.party ?? null,
            votes,
            share,
            seats,
          };
        })
        .toArray()
        .toSorted((a, b) => b.votes - a.votes);
      const level = grid?.level ?? (table == null ? null : first?.scheme);
      let filled = 0;
      for (const row of results) {
        filled += row.seats ?? 0;
      }
      const [map, previous] = await Promise.all([
        level == null || isBrief ? null : sketch(place, level, day),
        isBrief ||
        date == null ||
        head?.owner == null ||
        head.isOffice ||
        head.size == null ||
        filled < head.size * 0.9
          ? null
          : db.execute<{ party: string | null; seats: number }>(sql`
            with recursive last as (
              select max(e.date_from) as day
              from contest c join election e on e.id = c.election_id
              where coalesce(c.body_id, c.office_id) = ${head.owner}::uuid
                and c.allocation_parent_id is null and c.party_scope_id is null
                and e.kind = 'general' and e.date_from < ${date}::date - 120
            ),
            roots as (
              select c.id from contest c join election e on e.id = c.election_id
              left join seat st on st.id = c.seat_id
              where coalesce(c.body_id, c.office_id) = ${head.owner}::uuid
                and c.allocation_parent_id is null and c.party_scope_id is null and e.kind = 'general'
                and c.term is distinct from 'remainder' and coalesce(st.voting, true)
                and e.date_from between (select day from last) - 200 and (select day from last)
                and not exists (
                  select from contest pc join election pe on pe.id = pc.election_id
                  where pc.id = c.previous_round_id and pe.kind = 'general'
                )
            ),
            tree(id) as (
              select id from roots
              union all
              select c.id from contest c join roots r on c.previous_round_id = r.id
              union all
              select c.id from contest c join tree on c.allocation_parent_id = tree.id
            ),
            winners as materialized (
              select o.id, o.contest_id as contest, o.party_id, max(coalesce(x.seats, 1)) as seats
              from tree t
              join contest_option o on o.contest_id = t.id
              join outcome x on x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
              join result_set rs on rs.id = x.result_set_id and rs.is_default
              where not exists (select from contest k where k.allocation_parent_id = t.id)
              group by o.id, o.contest_id, o.party_id
            ),
            leads as (
              select distinct on (ch.parent_option_id) ch.parent_option_id as parent, ch.party_id
              from winners w
              join contest_option ch on ch.contest_id = w.contest and ch.parent_option_id = w.id and ch.party_id is not null
              join result_set rs on rs.contest_id = ch.contest_id and rs.is_default
              join result_rollup y on y.result_set_k = rs.k and y.option_k = ch.k
              where w.party_id is null
              group by ch.parent_option_id, ch.party_id, ch.k
              order by ch.parent_option_id, max(y.value) desc
            )
            select coalesce(pm.into_id, w.party_id, ld.party_id) as party, sum(w.seats)::int as seats
            from winners w
            left join leads ld on ld.parent = w.id
            left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
            group by 1
          `),
      ]);
      const prior = previous?.rows ?? [];
      let earlier = 0;
      for (const row of prior) {
        earlier += row.seats;
      }
      const before =
        previous == null || earlier < filled * 0.9 || earlier > filled * 1.1
          ? null
          : Map.groupBy(previous.rows, (row) =>
              row.party == null
                ? "none"
                : `party:${parentOf.get(row.party) ?? row.party}`,
            )
              .entries()
              .map(([key, rows]) => {
                let seats = 0;
                for (const row of rows) {
                  seats += row.seats;
                }
                return { key, seats };
              })
              .toArray();
      return {
        map,
        before,
        contest: single?.id ?? null,
        name:
          labels.full.get(
            (single == null ? head?.owner : single.id) ?? head?.id ?? "",
          ) ?? "",
        path: single == null ? null : (slugs.get(single.id) ?? null),
        kind: head?.kind ?? "",
        isOffice: head?.isOffice ?? false,
        isStage: head?.isStage ?? false,
        round: head?.round ?? null,
        office: head?.office ?? null,
        election: head?.election ?? "",
        day: head?.day ?? null,
        count: group.length,
        size: head?.size ?? null,
        spread: spread.rows,
        measure: here?.measure ?? "votes",
        valid: here?.side.valid ?? 0,
        results,
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
  return built
    .filter(
      (race) =>
        !stages.includes(race) &&
        stages.every((stage) => stage.election !== race.election),
    )
    .toSorted(
      (a, b) => (a.day ?? "").localeCompare(b.day ?? "") || b.valid - a.valid,
    )
    .map(({ isStage, office, election, day, spread, ...race }) => {
      const stage = stages.find((entry) => entry.office === office);
      const rows = (
        stage == null && race.measure === "electoral_votes" ? race : stage
      )?.results;
      return {
        ...race,
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
    });
}

export const elections = {
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}",
        summary: "Get an election and its contests",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        name: v.string(),
        date: v.nullable(v.string()),
        status: v.string(),
        breadcrumbs: v.array(Link),
        parent: v.nullable(Link),
        notes: v.array(v.object({ kind: v.string(), body: v.string() })),
        races: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            kind: v.string(),
            isOffice: v.boolean(),
            round: v.nullable(v.number()),
            count: v.number(),
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
            partners: v.array(v.array(v.string())),
            measure: v.string(),
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
              }),
            ),
            places: Places,
            before: v.nullable(
              v.array(v.object({ key: v.string(), seats: v.number() })),
            ),
            map: v.nullable(
              v.object({
                tiles: v.string(),
                own: v.nullable(v.object({ tiles: v.string(), k: v.number() })),
                frames: v.array(
                  v.object({
                    west: v.number(),
                    south: v.number(),
                    east: v.number(),
                    north: v.number(),
                  }),
                ),
                isApproximate: v.boolean(),
              }),
            ),
          }),
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
      }>(sql`
        select polity_id as polity, parent_id as parent, date_from::text as date, status::text as status
        from election where id = ${input.id}::uuid
      `);
      const election = found.rows[0];
      if (election == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const [contestRows, electionRows, notes, crumbs, raced] =
        await Promise.all([
          db.execute<{ id: string; children: number }>(sql`
            select c.id, (select count(*)::int from contest x where x.allocation_parent_id = c.id) as children
            from contest c where c.election_id = ${input.id}::uuid and c.allocation_parent_id is null
          `),
          db.execute<{
            id: string;
            date: string | null;
            kind: string;
            polity: string | null;
            noun: string | null;
          }>(sql`
            select e.id, e.date_from::text as date, e.kind::text as kind,
              nullif(e.polity_id, p.polity_id) as polity, sc.noun
            from election e
            join election p on p.id = e.parent_id
            left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
            left join area a on a.id = pt.area_id
            left join scheme sc on sc.code = a.scheme_code
            where e.parent_id = ${input.id}::uuid
          `),
          db.execute<{ kind: string; body: string }>(sql`
            select kind::text, body_md as body from note where entity_id = ${input.id}::uuid
          `),
          breadcrumbs(election.polity),
          races(input.id, election.polity, election.date),
        ]);
      const ids = [
        input.id,
        ...contestRows.rows.map((row) => row.id),
        ...electionRows.rows.flatMap((row) =>
          row.polity == null ? [row.id] : [row.id, row.polity],
        ),
        ...(election.parent == null ? [] : [election.parent]),
      ];
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      return {
        name: labels.full.get(input.id) ?? "",
        date: election.date,
        status: election.status,
        breadcrumbs: crumbs,
        parent:
          election.parent == null
            ? null
            : {
                name: labels.full.get(election.parent) ?? "",
                path: slugs.get(election.parent) ?? null,
              },
        notes: notes.rows,
        races: raced,
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
          }))
          .toSorted((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true }),
          ),
      };
    }),
  lead: os
    .meta(
      openapi({
        method: "GET",
        path: "/elections/{id}/lead",
        summary:
          "Get an election's first race with its top three by seats won, else by share",
      }),
    )
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.nullable(
        v.object({
          name: v.string(),
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
      let contested = 0;
      for (const row of race.results) {
        contested += row.seats ?? 0;
      }
      return {
        name: race.name,
        contested,
        results: (contested > 1
          ? race.results.toSorted((a, b) => (b.seats ?? 0) - (a.seats ?? 0))
          : race.results
        ).slice(0, 3),
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
      }>(sql`
        with here as (
          select e.id, e.polity_id, e.date_from,
            (select min(n.date_from) from election n
              where n.polity_id = e.polity_id and n.kind = 'general' and n.status = 'held'
                and n.parent_id is null and n.date_from > e.date_from) as next
          from election e where e.id = ${input.id}::uuid and e.kind = 'general'
        )
        select g.id, g.head_person_id as head, g.from_date::text as from, g.to_date::text as to,
          g.caretaker as "isCaretaker",
          g.from_date <= here.date_from and g.election_id is distinct from here.id as "isBefore",
          (select n.text from name n where n.entity_id = g.id
            order by n.kind = 'short' desc, n.is_preferred desc limit 1) as name
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
              seats: v.number(),
              parties: v.array(
                v.object({ party: v.nullable(v.string()), seats: v.number() }),
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
        region: string;
        scheme: string;
        party: string | null;
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
          where c.allocation_parent_id is null and c.party_scope_id is null
            and c.kind in ('body_seats', 'list_allocation')
            and c.term is distinct from 'remainder' and coalesce(st.voting, true)
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
          select l.id as leaf, l.root, o.id, o.party_id, o.alliance_id, max(coalesce(x.seats, 1))::int as seats
          from leaves l
          join result_set rs on rs.contest_id = l.id and rs.is_default
          join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and x.kind in ('elected', 'won')
          join contest_option o on o.id = x.option_id
          group by l.id, l.root, o.id, o.party_id, o.alliance_id
        ),
        leads as (
          select distinct on (ch.parent_option_id) ch.parent_option_id as parent, ch.party_id
          from winners w
          join contest_option ch on ch.contest_id = w.leaf and ch.parent_option_id = w.id and ch.party_id is not null
          join result_set rs on rs.contest_id = ch.contest_id and rs.is_default
          join result_rollup y on y.result_set_k = rs.k and y.option_k = ch.k
          where w.party_id is null
          group by ch.parent_option_id, ch.party_id, ch.k
          order by ch.parent_option_id, max(y.value) desc
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
          w.leaf, up.area as region, a.scheme_code as scheme,
          coalesce(bm.into_id, br.parent_party_id, pm.into_id, w.party_id, ld.party_id, w.alliance_id) as party, w.seats
        from winners w
        join roots r on r.id = w.root
        join up on up.leaf = w.leaf
        join area a on a.id = up.area
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
            .flatMap((rows) => {
              const leaves = new Set(rows.map((row) => row.leaf)).size;
              const chosen = Map.groupBy(rows, (row) => row.scheme)
                .entries()
                .map(([scheme, group]) => ({
                  scheme,
                  rows: group,
                  leaves: new Set(group.map((row) => row.leaf)).size,
                  regions: new Set(group.map((row) => row.region)).size,
                }))
                .filter(
                  (entry) => entry.leaves === leaves && entry.regions >= 4,
                )
                .toArray()
                .toSorted((a, b) => a.regions - b.regions)[0];
              const counted = chosen?.rows ?? [];
              let seats = 0;
              for (const row of counted) {
                seats += row.seats;
              }
              return chosen == null ||
                size == null ||
                seats < size * 0.9 ||
                seats > size * 1.02
                ? []
                : [
                    {
                      body: rows[0]?.body ?? "",
                      scheme: chosen.scheme,
                      seats,
                      rows: chosen.rows,
                    },
                  ];
            })
            .toSorted((a, b) => b.seats - a.seats)
            .slice(0, 1);
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
      const [labels, colors, positions, nouns] = await Promise.all([
        names(
          [
            ...best.map((chamber) => chamber.body),
            ...best.flatMap((chamber) => chamber.rows.map((row) => row.region)),
          ],
          null,
        ),
        refs([...parties, ...anchor.values()]),
        db.execute<{ party_id: string; lean: number }>(sql`
          select distinct on (party_id) party_id, (low + high) * 100 + coalesce(rank, 99) as lean from party_position
          where party_id = any(${sql.param([...parties, ...anchor.values()])}::uuid[])
          order by party_id, valid_from desc nulls last
        `),
        db.execute<{ code: string; noun: string | null }>(sql`
          select code, noun from scheme where code = any(${sql.param(best.map((chamber) => chamber.scheme))}::text[])
        `),
      ]);
      const lean = new Map(
        positions.rows.map((row) => [row.party_id, row.lean]),
      );
      const noun = new Map(nouns.rows.map((row) => [row.code, row.noun]));
      return best.map((chamber) => ({
        name: labels.full.get(chamber.body) ?? "",
        noun: noun.get(chamber.scheme) ?? null,
        regions: Map.groupBy(chamber.rows, (row) => row.region)
          .entries()
          .map(([region, rows]) => {
            const tally = Map.groupBy(rows, (row) => row.party ?? "")
              .entries()
              .map(([party, group]) => {
                let won = 0;
                for (const row of group) {
                  won += row.seats;
                }
                return { party: party === "" ? null : party, seats: won };
              })
              .toArray()
              .toSorted((a, b) => b.seats - a.seats);
            let won = 0;
            for (const entry of tally) {
              won += entry.seats;
            }
            return {
              name: labels.full.get(region) ?? "",
              seats: won,
              parties: tally,
            };
          })
          .toArray()
          .toSorted((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true }),
          ),
        parties: [
          ...new Set(chamber.rows.flatMap((row) => row.party ?? [])),
        ].map((id) => ({
          id,
          label: colors.get(id)?.label ?? "",
          short: colors.get(id)?.short ?? null,
          color: colors.get(anchor.get(id) ?? id)?.color ?? null,
          lean: lean.get(anchor.get(id) ?? id) ?? null,
        })),
      }));
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
                parties: v.array(
                  v.object({
                    party: v.nullable(v.string()),
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
        seats: number;
        races: number;
        passed: number;
      }>(sql`
        with parts as materialized (
          select e.id, e.polity_id, e.date_from from election e
          where e.parent_id = ${input.id}::uuid and e.kind not in ('runoff', 'indirect')
            and e.polity_id <> (select polity_id from election where id = ${input.id}::uuid)
        ),
        roots as materialized (
          select c.id, p.id as part,
            case when c.kind = 'ballot_question' then 'measure'
              when o.kind = 'head_of_state' or c.kind = 'elector_slate' then 'top'
              when o.kind = 'governor' then 'governor'
              when b.kind = 'lower_house' then 'lower'
              else 'upper' end as tier
          from parts p
          join contest c on c.election_id = p.id
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
          select r.id as contest, r.part, r.tier, o.id, o.party_id, max(coalesce(x.seats, 1)) as seats
          from roots r
          join result_set rs on rs.contest_id = r.id and rs.is_default
          join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and x.kind in ('elected', 'won')
          join contest_option o on o.id = x.option_id
          where r.tier <> 'measure'
          group by r.id, r.part, r.tier, o.id, o.party_id
        ),
        lines as materialized (
          select ch.parent_option_id as parent, ch.party_id, ch.k, ch.contest_id
          from winners w join contest_option ch on ch.contest_id = w.contest and ch.parent_option_id = w.id
          where w.party_id is null and ch.party_id is not null
        ),
        leads as (
          select distinct on (l.parent) l.parent, l.party_id
          from lines l
          join result_set rs on rs.contest_id = l.contest_id and rs.is_default
          join result_rollup y on y.result_set_k = rs.k and y.option_k = l.k
          group by l.parent, l.party_id, l.k
          order by l.parent, max(y.value) desc
        ),
        measures as (
          select r.part, count(*)::int as races,
            count(*) filter (where exists (
              select from result_set rs
              join outcome x on x.result_set_id = rs.id and x.kind = 'won'
              join contest_option o on o.id = x.option_id
              where rs.contest_id = r.id and rs.is_default and o.ballot_label ~* '^(yes|for|approve)'
            ))::int as passed
          from roots r where r.tier = 'measure'
          group by r.part
        )
        select w.part, p.polity_id as polity, w.tier, coalesce(bm.into_id, br.parent_party_id, pm.into_id, w.party_id, ld.party_id) as party,
          sum(w.seats)::int as seats, count(distinct w.contest)::int as races, 0 as passed
        from winners w
        join parts p on p.id = w.part
        left join leads ld on ld.parent = w.id
        left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
        left join party_affiliation br on br.party_id = coalesce(pm.into_id, w.party_id, ld.party_id)
          and br.kind = 'regional_branch' and br.valid @> p.date_from
        left join entity_merge bm on bm.entity_id = br.parent_party_id
        group by w.part, p.polity_id, w.tier, coalesce(bm.into_id, br.parent_party_id, pm.into_id, w.party_id, ld.party_id)
        union all
        select m.part, p.polity_id, 'measure', null, 0, m.races, m.passed
        from measures m join parts p on p.id = m.part
        union all
        select p.id, p.polity_id, null, null, 0, 0, 0 from parts p
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
                for (const row of group) {
                  races += row.races;
                  passed += row.passed;
                }
                return {
                  part,
                  races,
                  passed,
                  parties: group
                    .filter((row) => row.seats > 0)
                    .map((row) => ({ party: row.party, seats: row.seats }))
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

export const polities = {
  list: os
    .meta(
      openapi({ method: "GET", path: "/polities", summary: "List polities" }),
    )
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
    .handler(async () => {
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
        )
        select p.id, p.kind::text, p.iso_code,
          (select count(*)::int from election e where e.polity_id = p.id) as elections,
          (select count(*)::int from election e join below b on b.id = e.polity_id and b.top = p.id) as within,
          (select min(extract(year from e.date_from))::int from election e where e.polity_id = p.id) as first,
          (select max(extract(year from e.date_from))::int from election e where e.polity_id = p.id) as last,
          (select e.id from election e where e.polity_id = p.id and e.status = 'held' and e.date_from <= current_date
             and not exists (select from election x where x.id = e.parent_id and x.polity_id = e.polity_id)
           order by e.kind = 'general' desc, e.date_from desc limit 1) as latest
        from polity p
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
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        name: v.string(),
        kind: v.string(),
        breadcrumbs: v.array(Link),
        polities: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            kind: v.string(),
            elections: v.number(),
            first: v.nullable(v.number()),
            last: v.nullable(v.number()),
          }),
        ),
        within: v.number(),
        results: v.array(
          v.object({
            year: v.nullable(v.number()),
            date: v.string(),
            races: v.array(
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
          }),
        ),
        latest: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            races: v.array(
              v.object({
                contest: v.nullable(v.string()),
                name: v.string(),
                path: v.nullable(v.string()),
                count: v.number(),
                contested: v.number(),
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
      const [found, subdivisions, electionRows, crumbs, here] =
        await Promise.all([
          db.execute<{ kind: string }>(
            sql`select kind::text from polity where id = ${input.id}::uuid`,
          ),
          db.execute<{
            id: string;
            kind: string;
            elections: number;
            first: number | null;
            last: number | null;
            within: number;
          }>(sql`
            with recursive below(id, top) as (
              select distinct polity_id, polity_id from polity_parent where parent_id = ${input.id}::uuid
              union
              select p.polity_id, below.top from polity_parent p join below on p.parent_id = below.id
            )
            select below.top as id, (select kind::text from polity where id = below.top) as kind, count(e.id)::int as elections,
              min(extract(year from e.date_from))::int as first, max(extract(year from e.date_from))::int as last,
              sum(count(e.id)) over ()::int as within
            from below left join election e on e.polity_id = below.id
              and not exists (select from election p where p.id = e.parent_id and p.polity_id = e.polity_id)
            group by below.top
          `),
          db.execute<{
            id: string;
            date: string | null;
            year: number | null;
            kind: string;
            status: string;
          }>(sql`
            select e.id, e.date_from::text as date, ${CYCLE} as year, e.kind::text as kind, e.status::text as status from election e
            where e.polity_id = ${input.id}::uuid
              and not exists (select from election p where p.id = e.parent_id and p.polity_id = e.polity_id)
            order by e.date_from desc nulls last
          `),
          breadcrumbs(input.id),
          db.execute<{
            id: string;
            parent: string | null;
            path: string;
            date: string;
            year: number | null;
            area: string;
            k: number;
          }>(sql`
            with recursive up(id) as (
              select parent_id from polity_parent where polity_id = ${input.id}::uuid
              union
              select p.parent_id from polity_parent p join up on p.polity_id = up.id
            ),
            place as (
              select a.id, a.k from polity_territory t join area a on a.id = t.area_id
              join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
              where t.polity_id = ${input.id}::uuid
              order by upper(t.valid) desc nulls first limit 1
            )
            select c.id, c.allocation_parent_id as parent, s.path, e.date_from::text as date, ${CYCLE} as year,
              place.id as area, place.k
            from place, election e
            join contest c on c.election_id = e.id
            join slug s on s.entity_id = c.id and s.is_current
            where e.polity_id in (select id from up) and e.date_from is not null and exists (
              select 1 from result_set rs join result_rollup r on r.result_set_k = rs.k
              where rs.contest_id = c.id and rs.status = 'active' and r.area_k = place.k and r.measure = any(${COUNTED})
            )
          `),
        ]);
      const polity = found.rows[0];
      if (polity == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const ids = [
        input.id,
        ...subdivisions.rows.map((row) => row.id),
        ...electionRows.rows.map((row) => row.id),
      ];
      const today = new Date().toISOString().slice(0, 10);
      const held = electionRows.rows.filter(
        (row) => row.date != null && row.date <= today && row.status === "held",
      );
      const recent =
        held.find((row) => row.kind === "general") ?? held[0] ?? null;
      const parents = new Set(here.rows.map((row) => row.parent));
      const kept = here.rows.filter((row) => !parents.has(row.id));
      const [labels, slugs, raced, built] = await Promise.all([
        names(ids),
        paths(ids),
        recent == null ? [] : races(recent.id, input.id, recent.date, true),
        Promise.all(
          kept.map(async (row) => {
            const other = await find(row.id);
            if (other.place == null) {
              return null;
            }
            const kids =
              other.place.k === row.k
                ? []
                : await children(row.id, other.place, true, row.date);
            const unit =
              other.place.k === row.k
                ? ""
                : kids.find((kid) => kid.k === row.k)?.segment;
            if (unit == null) {
              return null;
            }
            const [totals, label] = await Promise.all([
              load([row.id], [row.k], row.date, [row.id]),
              names([row.id], row.date),
            ]);
            const loaded = totals.get(row.k);
            return {
              ...row,
              name: label.full.get(row.id) ?? "",
              path: [row.path, unit].filter((part) => part !== "").join("/"),
              leaders:
                loaded == null
                  ? []
                  : compare(loaded.side, null, [], "party")
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
        ),
      ]);
      return {
        name: labels.full.get(input.id) ?? "",
        kind: polity.kind,
        breadcrumbs: crumbs.slice(0, -1),
        polities: subdivisions.rows
          .map((row) => ({
            name: labels.full.get(row.id) ?? "",
            path: slugs.get(row.id) ?? null,
            kind: row.kind,
            elections: row.elections,
            first: row.first,
            last: row.last,
          }))
          .toSorted((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true }),
          ),
        within: subdivisions.rows[0]?.within ?? 0,
        results: Map.groupBy(
          built
            .filter((race) => race != null)
            .filter((race) => race.leaders.length > 0)
            .toSorted(
              (a, b) =>
                b.date.localeCompare(a.date) ||
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
              leaders: race.leaders,
            })),
          }))
          .toArray(),
        elections: electionRows.rows.map((row) => ({
          name: labels.full.get(row.id) ?? "",
          path: slugs.get(row.id) ?? null,
          date: row.date,
          year: row.year,
          kind: row.kind,
          status: row.status,
        })),
        latest:
          recent == null
            ? null
            : {
                name: labels.full.get(recent.id) ?? "",
                path: slugs.get(recent.id) ?? null,
                date: recent.date,
                races: raced.map((race) => {
                  let contested = 0;
                  for (const row of race.results) {
                    contested += row.seats ?? 0;
                  }
                  return {
                    contest: race.contest,
                    name: race.name,
                    path: race.path,
                    count: race.count,
                    contested,
                    measure: race.measure,
                    results: race.results.slice(0, 3),
                  };
                }),
              },
      };
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
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
    .output(
      v.object({
        series: v.array(
          v.object({
            id: v.string(),
            name: v.string(),
            isOffice: v.boolean(),
            points: v.array(
              v.object({
                name: v.string(),
                path: v.nullable(v.string()),
                date: v.string(),
                year: v.number(),
                races: v.number(),
                filled: v.number(),
                share: v.nullable(v.number()),
                parties: v.array(
                  v.object({
                    party: v.nullable(v.string()),
                    seats: v.number(),
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
      const found = await db.execute<{
        seat: string;
        isOffice: boolean;
        bucket: string;
        link: string | null;
        date: string;
        year: number;
        races: number;
        party: string | null;
        seats: number;
        share: number | null;
      }>(sql`
        with recursive roots as materialized (
          select c.id, coalesce(c.office_id, c.body_id) as seat, c.office_id is not null as "isOffice",
            e.date_from as date, ${CYCLE} as year, a.k as area,
            case when e.polity_id = ${input.id}::uuid then e.id
              when g.polity_id = ${input.id}::uuid then g.id
              else (
                select n.id from election n
                where n.polity_id = ${input.id}::uuid and n.kind = e.kind
                  and extract(year from n.date_from) = extract(year from e.date_from)
                order by n.parent_id is null desc, abs(n.date_from - e.date_from) limit 1
              ) end as link
          from contest c
          join election e on e.id = c.election_id
          join election g on g.id = coalesce(e.parent_id, e.id)
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          where c.allocation_parent_id is null and c.party_scope_id is null
            and e.kind in ('general', 'local') and c.kind in ('body_seats', 'list_allocation', 'office')
            and not exists (
              select from contest pc join election pe on pe.id = pc.election_id
              where pc.id = c.previous_round_id and pe.kind in ('general', 'local')
            )
            and coalesce(
              (select polity_id from body where id = c.body_id),
              (select polity_id from office where id = c.office_id)
            ) = ${input.id}::uuid
        ),
        rounds as (
          select r.id as root, r.id from roots r
          union all
          select r.id, c.id from roots r join contest c on c.previous_round_id = r.id where r."isOffice"
        ),
        tree(root, id) as (
          select root, id from rounds
          union all
          select tree.root, c.id from contest c join tree on c.allocation_parent_id = tree.id
        ),
        winners as materialized (
          select t.root, t.id as contest, o.id, o.party_id, max(coalesce(x.seats, 1)) as seats
          from tree t
          join contest_option o on o.contest_id = t.id
          join outcome x on x.option_id = o.id and x.kind in ('elected', 'won') and x.candidacy_id is null
          join result_set rs on rs.id = x.result_set_id and rs.is_default
          where not exists (select from contest k where k.allocation_parent_id = t.id)
          group by t.root, t.id, o.id, o.party_id
        ),
        lines as materialized (
          select ch.parent_option_id as parent, ch.party_id, ch.k, ch.contest_id
          from winners w join contest_option ch on ch.contest_id = w.contest and ch.parent_option_id = w.id
          where w.party_id is null and ch.party_id is not null
        ),
        leads as (
          select distinct on (l.parent) l.parent, l.party_id
          from lines l
          join result_set rs on rs.contest_id = l.contest_id and rs.is_default
          join result_rollup y on y.result_set_k = rs.k and y.option_k = l.k
          group by l.parent, l.party_id, l.k
          order by l.parent, max(y.value) desc
        ),
        won as materialized (
          select w.root, w.contest, w.id, w.seats, coalesce(pm.into_id, w.party_id, ld.party_id) as party
          from winners w
          left join leads ld on ld.parent = w.id
          left join entity_merge pm on pm.entity_id = coalesce(w.party_id, ld.party_id)
        ),
        fetched as (
          select r.id as root, coalesce(o.parent_option_id, o.id) as top, x.measure, x.value::float8 as value,
            x.count_round, max(x.count_round) over (partition by r.id, x.measure) as first
          from (select id, area from roots where "isOffice") r
          join (select distinct root, contest from won) w on w.root = r.id
          join result_set rs on rs.contest_id = w.contest and rs.is_default
          join result_rollup x on x.result_set_k = rs.k and x.area_k = r.area and x.measure = any(${COUNTED}) and x.option_k <> 0
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
        )
        select r.seat, bool_or(r."isOffice") as "isOffice", coalesce(r.link::text, r.year::text) as bucket,
          (array_agg(r.link))[1] as link,
          min(r.date)::text as date, min(r.year) as year, count(distinct r.id)::int as races,
          w.party, sum(w.seats)::int as seats, avg(sh.share)::float8 as share
        from roots r
        join won w on w.root = r.id
        left join shares sh on sh.root = r.id
        group by r.seat, coalesce(r.link::text, r.year::text), w.party
        order by min(r.date)
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
            order by n.kind = 'short' desc, n.is_preferred desc limit 1) as name,
          (select coalesce(pm.into_id, gp.party_id) from government_party gp
            left join entity_merge pm on pm.entity_id = gp.party_id
            where gp.government_id = g.id
            order by gp.role = 'head' desc, gp.seats desc nulls last limit 1) as party
        from government g where g.polity_id = ${input.id}::uuid
        order by g.from_date
      `);
      const seats = [...new Set(found.rows.map((row) => row.seat))];
      const parties = [
        ...new Set([
          ...found.rows.flatMap((row) => row.party ?? []),
          ...cabinets.rows.flatMap((row) => row.party ?? []),
        ]),
      ];
      const links = [...new Set(found.rows.flatMap((row) => row.link ?? []))];
      const [labels, slugs, colors, positions] = await Promise.all([
        names([
          ...seats,
          ...links,
          ...parties,
          ...cabinets.rows.flatMap((row) => row.head ?? []),
        ]),
        paths(links),
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
      return {
        series: seats
          .map((seat) => {
            const rows = found.rows.filter((row) => row.seat === seat);
            return {
              id: seat,
              name: labels.full.get(seat) ?? "",
              isOffice: rows.some((row) => row.isOffice),
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
                      (first?.link == null
                        ? null
                        : labels.full.get(first.link)) ??
                      `${first?.year ?? ""} ${labels.full.get(seat) ?? ""}`.trim(),
                    path:
                      first?.link == null
                        ? null
                        : (slugs.get(first.link) ?? null),
                    date: first?.date ?? "",
                    year: first?.year ?? 0,
                    races: Math.max(...group.map((row) => row.races)),
                    filled,
                    share:
                      group.length === 1 && first?.isOffice === true
                        ? first.share
                        : null,
                    parties: group
                      .map((row) => ({ party: row.party, seats: row.seats }))
                      .toSorted((a, b) => b.seats - a.seats),
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
                      .map(([party, seats]) => ({ party, seats }))
                      .toSorted((x, y) => y.seats - x.seats);
                    return [];
                  }
                  return [point];
                }),
            };
          })
          .filter((entry) => entry.points.length > 1)
          .toSorted(
            (a, b) =>
              Number(b.isOffice) - Number(a.isOffice) ||
              Math.max(...b.points.map((point) => point.filled)) -
                Math.max(...a.points.map((point) => point.filled)),
          ),
        governments: cabinets.rows.map((row) => ({
          name: row.name ?? "",
          head: row.head == null ? null : (labels.full.get(row.head) ?? null),
          party: row.party,
          from: row.from,
          to: row.to,
          isCaretaker: row.isCaretaker,
        })),
        parties: parties.map((id) => ({
          ...(colors.get(id) ?? { id, label: "", short: null, color: null }),
          lean: lean.get(id) ?? null,
        })),
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
      const found = await db.execute<{
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
          select distinct on (t.polity_id) t.polity_id as polity, a.id as area, a.k
          from polity_territory t join area a on a.id = t.area_id
          where t.polity_id in (${input.a}::uuid, ${input.b}::uuid)
          order by t.polity_id, upper(t.valid) desc nulls first
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
          select c.id from election e join contest c on c.election_id = e.id
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
          join contest c on c.id = t.id
          join result_set rs on rs.contest_id = t.id and rs.status = 'active'
          join place pl on c.district_area_id is null
            or c.district_area_id in (select u.area from ups u where u.polity = pl.polity)
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
        select c.id,
          case when c.kind = 'ballot_question' then 'question'
            else concat_ws(':', coalesce(c.office_id, c.body_id), c.kind, c.round_number) end as series,
          coalesce(c.office_id, c.body_id) as owner, c.kind = 'ballot_question' as "isQuestion",
          e.date_from::text as date, ${CYCLE} as year, coalesce(own.k, home.k) as own,
          (select k from place where polity = ${input.a}::uuid) as a,
          (select k from place where polity = ${input.b}::uuid) as b
        from hits h
        join contest c on c.id = h.root
        join election e on e.id = c.election_id
        left join area own on own.id = c.district_area_id
        left join lateral (
          select a.k from polity_territory t join area a on a.id = t.area_id
          where t.polity_id = e.polity_id
          order by t.valid @> e.date_from desc, upper(t.valid) desc nulls first
          limit 1
        ) home on true
        order by e.date_from
      `);
      const [labels, slugs, loaded] = await Promise.all([
        names(
          [
            input.a,
            input.b,
            ...found.rows.flatMap((row) =>
              row.owner == null ? [row.id] : [row.id, row.owner],
            ),
          ],
          null,
        ),
        paths([input.a, input.b, ...found.rows.map((row) => row.id)]),
        Promise.all(
          found.rows.map(async (row) =>
            load(
              [row.id],
              [row.own, row.a, row.b].filter((k) => k != null),
              row.date,
              [row.id],
            ),
          ),
        ),
      ]);
      const points = found.rows.map((row, index) => ({
        ...row,
        sides: [row.own, row.a, row.b].map((k) => {
          const here = k == null ? null : loaded[index]?.get(k);
          return here == null
            ? []
            : compare(here.side, null, [], "party")
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
                .slice(0, 4);
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
                : (labels.full.get(group[0].owner) ?? null),
            isQuestion: group[0]?.isQuestion ?? false,
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
            (first, second) => second.points.length - first.points.length,
          ),
      };
    }),
};

async function refs(ids: Array<string>) {
  const unique = [...new Set(ids)];
  const [labels, colors] = await Promise.all([
    names(unique),
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
    order by entity_id
  `);
  const ids = found.rows.map((row) => row.id);
  const labels = await names(ids);
  return ids.filter((id) => segment(labels.full.get(id) ?? "") === base);
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
  return peers[Number(suffix[2]) - 1] ?? null;
}

export const entities = {
  path: os
    .meta(
      openapi({
        method: "GET",
        path: "/entities/{id}/path",
        summary:
          "Resolve any entity's ID to its canonical page path: a polity's, election's, or contest's slug, /people/{slug} for a person, and /parties/{polity}/{slug} for a party",
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
      }>(sql`
        select e.id, e.type::text as type,
          (select s.path from slug s where s.entity_id = e.id and s.is_current) as slug,
          (select s.path from party p join slug s on s.entity_id = p.polity_id and s.is_current where p.id = e.id) as polity
        from entity e
        where e.id = coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
      `);
      const entity = found.rows[0];
      if (entity == null) {
        throw new ORPCError("NOT_FOUND");
      }
      if (entity.slug != null) {
        return { path: entity.slug };
      }
      if (entity.type !== "person" && entity.type !== "party") {
        return { path: null };
      }
      const labels = await names([entity.id]);
      const base = segment(labels.full.get(entity.id) ?? "");
      const owner =
        entity.type === "party"
          ? await db.execute<{ polity: string | null }>(sql`
            select polity_id as polity from party where id = ${entity.id}::uuid
          `)
          : null;
      const peers = await namesakes(
        entity.type,
        base,
        owner?.rows[0]?.polity ?? null,
      );
      const at = peers.indexOf(entity.id);
      const slug = at > 0 ? `${base}-${at + 1}` : base;
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
        wikidata: v.nullable(v.string()),
        party: v.nullable(PartyRef),
        record: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.string(),
            year: v.number(),
            kind: v.string(),
            measure: v.string(),
            votes: v.nullable(v.number()),
            total: v.nullable(v.number()),
            outcome: v.nullable(v.string()),
            electoral: v.nullable(
              v.object({ votes: v.number(), total: v.number() }),
            ),
            party: v.nullable(PartyRef),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        born: string | null;
        precision: string | null;
        wikidata: string | null;
      }>(sql`
        select p.id, p.birth_date::text as born, p.birth_precision::text as precision,
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
        measure: number | null;
        votes: number | null;
        total: number | null;
        outcome: string | null;
        party: string | null;
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
            coalesce(pm.into_id, lp.party) as party
          from candidacy cd join contest_option o on o.id = cd.option_id
          cross join lateral (
            select coalesce(cd.member_of_party_id, o.party_id, ${fused(sql`o.id`, sql`o.contest_id`)}) as party
          ) lp
          left join entity_merge pm on pm.entity_id = lp.party
          where cd.person_id in (select id from ids) and cd.role in ('head', 'list_member')
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
          select c.id as root, a.k as area, e.date_from as date, ${CYCLE} as year, e.kind::text as kind
          from contest c join election e on e.id = c.election_id
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          join area a on a.id = coalesce(c.district_area_id, pt.area_id)
          where c.id in (select id from up where parent is null)
        ),
        picked as (
          select distinct on (rs.contest_id) grp.root, rs.k, p.area
          from grp join result_set rs on rs.contest_id = grp.id join placed p on p.root = grp.root
          join lateral (
            select 1 from result_rollup r where r.result_set_k = rs.k and r.area_k = p.area and r.measure = any(${COUNTED}) limit 1
          ) hit on true
          order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
        ),
        fetched as (
          select p.root, r.* from picked p cross join lateral (
            select r.option_k as option, r.measure, r.value::float8 as value, r.count_round,
              min(r.count_round) over (partition by r.measure) as first
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
        totals as (
          select f.root, m.main, m.appointed, sum(f.value) as total,
            coalesce(sum(f.value) filter (where f.option in (select k from lines)), 0) as votes
          from fetched f join mains m on m.root = f.root and m.main = f.measure
          where f.count_round = f.first and f.option <> 0
          group by f.root, m.main, m.appointed
        )
        select p.root, p.date::text as date, p.year, p.kind, t.main::int as measure, t.votes,
          case when t.main = 11 then coalesce(t.appointed, t.total) else t.total end as total,
          (select (array_agg(x.kind::text order by array_position(array['elected', 'advanced', 'won', 'tied']::outcome_kind[], x.kind)))[1]
            from mine m join outcome x on x.option_id = m.option
              and (x.candidacy_id = m.candidacy or (x.candidacy_id is null and m.role = 'head'))
            join result_set rs on rs.id = x.result_set_id and rs.is_default
            where m.contest_id = p.root) as outcome,
          lead.party,
          (select n.text from name n where n.entity_id = lead.party and n.kind in ('official', 'english')
            order by coalesce(n.valid @> p.date, false) desc, n.is_preferred desc, (n.lang = 'en') desc,
              (n.kind = 'official') desc limit 1) as party_label,
          (select n.text from name n where n.entity_id = lead.party and n.kind = 'abbreviation'
            order by coalesce(n.valid @> p.date, false) desc, n.is_preferred desc, (n.lang = 'en') desc
            limit 1) as party_short,
          coalesce(${paint(sql`lead.party`, sql`select election_id from contest where id = p.root`, sql`p.date`)},
            (select pc.color from party_color pc where pc.party_id = lead.party
              order by upper(pc.valid) desc nulls first limit 1)) as party_color
        from placed p left join totals t on t.root = p.root
        left join lateral (
          select m.party from mine m join grp g on g.id = m.contest_id and g.root = p.root where m.party is not null limit 1
        ) lead on true
        order by p.date desc
      `);
      const roots = rows.rows.map((row) => row.root);
      const [labels, slugs] = await Promise.all([
        names([person.id, ...roots]),
        paths(roots),
      ]);
      const record = rows.rows
        .filter(
          (row) =>
            row.kind !== "indirect" ||
            rows.rows.every(
              (peer) => !(peer.kind === "general" && peer.year === row.year),
            ),
        )
        .map((row) => {
          const indirect =
            row.kind === "general"
              ? rows.rows.find(
                  (peer) => peer.kind === "indirect" && peer.year === row.year,
                )
              : undefined;
          return {
            name: labels.full.get(row.root) ?? "",
            path: slugs.get(row.root) ?? null,
            date: row.date,
            year: row.year,
            kind: row.kind,
            measure: MEASURES.get(row.measure ?? 1) ?? "votes",
            votes: row.votes,
            total: row.total,
            outcome: indirect == null ? row.outcome : indirect.outcome,
            electoral:
              indirect?.votes == null || indirect.total == null
                ? null
                : { votes: indirect.votes, total: indirect.total },
            party:
              row.party == null
                ? null
                : {
                    id: row.party,
                    label: row.party_label ?? "",
                    short: row.party_short,
                    color: row.party_color,
                  },
          };
        });
      return {
        name: labels.full.get(person.id) ?? "",
        born: person.born,
        precision: person.precision,
        wikidata: person.wikidata,
        party: record.find((entry) => entry.party != null)?.party ?? null,
        record,
      };
    }),
};

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
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
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
        total: number;
      }>(sql`
        with recursive ids(id) as (
          select coalesce((select into_id from entity_merge where entity_id = ${input.id}::uuid), ${input.id}::uuid)
          union
          select em.entity_id from entity_merge em join ids on em.into_id = ids.id
        ),
        mine as materialized (
          select o.id as option, o.contest_id from contest_option o where o.party_id in (select id from ids)
        ),
        up(id, parent) as (
          select c.id, c.allocation_parent_id from contest c where c.id in (select contest_id from mine)
          union
          select c.id, c.allocation_parent_id from up join contest c on c.id = up.parent
        ),
        won as (
          select distinct m.contest_id from mine m
          join outcome x on x.option_id = m.option and x.candidacy_id is null and x.kind in ('elected', 'won')
          join result_set rs on rs.id = x.result_set_id and rs.is_default
        )
        select coalesce(e.parent_id, e.id) as election, min(e.date_from)::text as date, min(${CYCLE}) as year,
          count(distinct up.id)::int as races,
          count(distinct w.contest_id)::int as won,
          count(*) over ()::int as total
        from up join contest c on c.id = up.id join election e on e.id = c.election_id
        left join won w on w.contest_id = up.id
        where up.parent is null
        group by coalesce(e.parent_id, e.id)
        order by min(e.date_from) desc nulls last
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
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
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
            }),
          ),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        grp: string | null;
        seat: string;
        root: string;
        date: string;
        year: number;
        races: number;
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
        home as (
          select polity_id as polity from party where id = (select id from ids limit 1)
        ),
        mine as materialized (
          select o.id as option, o.contest_id from contest_option o where o.party_id in (select id from ids)
        ),
        up(id, parent) as (
          select c.id, c.allocation_parent_id from contest c where c.id in (select contest_id from mine)
          union
          select c.id, c.allocation_parent_id from up join contest c on c.id = up.parent
        ),
        entered as materialized (
          select distinct coalesce(e.parent_id, e.id) as grp, coalesce(c.body_id, c.office_id) as seat
          from up join contest c on c.id = up.id join election e on e.id = c.election_id
          where up.parent is null and coalesce(c.body_id, c.office_id) is not null
            and c.party_scope_id is null and e.kind in ('general', 'local')
            and ((select polity from home) is null or coalesce(
              (select polity_id from body where id = c.body_id),
              (select polity_id from office where id = c.office_id)
            ) = (select polity from home))
        ),
        roots as materialized (
          select c.id, n.grp, n.seat, e.date_from as date, ${CYCLE} as year, c.round_number, a.k as area,
            case when g.polity_id = (select polity from home) then n.grp::text else (${CYCLE})::text end as bucket
          from entered n join election g on g.id = n.grp join election e on coalesce(e.parent_id, e.id) = n.grp
          join contest c on c.election_id = e.id and coalesce(c.body_id, c.office_id) = n.seat
            and c.allocation_parent_id is null and c.party_scope_id is null
          left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
          left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
        ),
        tree(root, id, area) as (
          select id, id, area from roots where round_number = 1
          union all
          select tree.root, c.id, tree.area from contest c join tree on c.allocation_parent_id = tree.id
        ),
        picked as (
          select distinct on (rs.contest_id) t.root, rs.k, t.area
          from tree t join result_set rs on rs.contest_id = t.id
          where exists (
            select 1 from result_rollup x where x.result_set_k = rs.k and x.area_k = t.area and x.measure = any(${COUNTED}) limit 1
          )
          order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
        ),
        fetched as (
          select p.root, x.option_k, x.measure, x.value::float8 as value, x.count_round,
            min(x.count_round) over (partition by p.root, x.measure) as first
          from picked p join result_rollup x on x.result_set_k = p.k and x.area_k = p.area
            and x.measure = any(${COUNTED}) and x.option_k <> 0
        ),
        mains as (
          select root, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main from fetched group by root
        ),
        tallied as (
          select f.root, sum(f.value) as total,
            coalesce(sum(f.value) filter (
              where o.party_id in (select id from ids) or o.parent_option_id in (select option from mine)
            ), 0) as votes
          from fetched f join mains m on m.root = f.root and m.main = f.measure
          join contest_option o on o.k = f.option_k
          where f.count_round = f.first
          group by f.root
        ),
        seated as (
          select w.root, coalesce(sum(w.seats) filter (where w.mine), 0) as seats, sum(w.seats) as filled
          from (
            select r.id as root, o.id,
              bool_or(coalesce(o.party_id, ${fused(sql`o.id`, sql`o.contest_id`)}) in (select id from ids)) as mine,
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
            join result_set rs on rs.id = x.result_set_id and rs.is_default
            where not exists (select from contest k where k.allocation_parent_id = sub.id)
              or sub.id = r.id and not exists (select from contest k where k.allocation_parent_id = r.id)
            group by r.id, o.id
          ) w
          group by w.root
        )
        select case when count(distinct r.grp) = 1 then min(r.grp::text) end as grp, r.seat,
          (array_agg(r.id order by r.id))[1] as root, min(r.date)::text as date,
          min(r.year) as year, count(distinct r.id)::int as races,
          sum(t.votes)::float8 as votes, sum(t.total)::float8 as total,
          sum(s.seats)::int as seats, sum(s.filled)::int as filled
        from roots r left join tallied t on t.root = r.id left join seated s on s.root = r.id
        group by r.bucket, r.seat
        order by min(r.date)
      `);
      const seats = [...new Set(found.rows.map((row) => row.seat))];
      const linked = found.rows.flatMap((row) => [
        ...(row.races === 1 ? [row.root] : []),
        ...(row.grp == null ? [] : [row.grp]),
      ]);
      const [labels, slugs] = await Promise.all([
        names([...seats, ...linked]),
        paths(linked),
      ]);
      return seats.map((seat) => ({
        id: seat,
        name: labels.full.get(seat) ?? "",
        points: found.rows
          .filter((row) => row.seat === seat)
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
            };
          }),
      }));
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
    .input(v.object({ id: v.pipe(v.string(), v.uuid()) }))
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
          where o.party_id in (select id from ids)
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
          other: string;
        }>(sql`
          select r.kind::text, r.from_party_id = ${party.id}::uuid as outgoing, r.date::text,
            case when r.from_party_id = ${party.id}::uuid then r.to_party_id else r.from_party_id end as other
          from party_relation r
          where (r.from_party_id = ${party.id}::uuid or r.to_party_id = ${party.id}::uuid) and r.to_party_id is not null
          order by r.date nulls last
        `),
        db.execute<{ kind: string; parent: string }>(sql`
          select distinct on (parent_party_id) kind::text, parent_party_id as parent
          from party_affiliation where party_id = ${party.id}::uuid
          order by parent_party_id, upper(valid) desc nulls first
        `),
      ]);
      const polity = party.polity == null ? [] : [party.polity];
      const others = relations.rows.map((row) => row.other);
      const [entities, slugs, spans] = await Promise.all([
        refs([
          party.id,
          ...polity,
          ...relations.rows.map((row) => row.other),
          ...parents.rows.map((row) => row.parent),
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
          "Search polities, elections, contests, parties, and people by name, accents and case ignored",
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
        }),
      ),
    )
    .handler(async ({ input }) => {
      if (input.q === "") {
        return [];
      }
      const found = await db.execute<{
        id: string;
        type: string;
        target: string | null;
        context: string | null;
        date: string | null;
      }>(sql`
        with q as (select lower(unaccent(${input.q})) as t),
        terms as (
          select to_tsquery('simple', string_agg(quote_literal(w) || ':*', ' & ')) as tq
          from q, regexp_split_to_table(q.t, '\\s+') w where w <> ''
        ),
        hits as (
          select d.entity_id, d.type, ts_rank(d.tsv, terms.tq) + word_similarity(q.t, d.text) + d.rank_boost * 0.15 as score
          from search_document d, q, terms
          where d.tsv @@ terms.tq
          union all
          select d.entity_id, d.type, word_similarity(q.t, d.text) + d.rank_boost * 0.15
          from search_document d, q, terms
          where q.t <% d.text and not d.tsv @@ terms.tq
        ),
        top as (select * from hits order by score desc limit ${input.limit * 2})
        select top.entity_id as id, top.type::text as type, x.target, x.context,
          coalesce(x.date, (select e.date_from from contest c join election e on e.id = c.election_id where c.id = x.target and top.type = 'person'))::text as date
        from top
        cross join lateral (
          select
            case top.type
              when 'person' then (
                select c.id from candidacy cy
                join contest_option o on o.id = cy.option_id join contest c on c.id = o.contest_id
                join election e on e.id = c.election_id
                where cy.person_id = top.entity_id order by e.date_from desc nulls last limit 1)
              when 'party' then (
                select c.id from contest_option o join contest c on c.id = o.contest_id
                join election e on e.id = c.election_id
                where o.party_id = top.entity_id and c.allocation_parent_id is null
                order by e.date_from desc nulls last limit 1)
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
        order by top.score desc
      `);
      const rows = found.rows.filter((row) => row.target != null);
      const ids = rows.flatMap((row) =>
        [row.id, row.target, row.context].filter((id) => id != null),
      );
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      return rows
        .map((row) => ({
          id: row.id,
          type: row.type,
          name: labels.full.get(row.id) ?? "",
          path:
            row.type === "person" || row.type === "party"
              ? `id/${row.id}`
              : (slugs.get(row.target ?? "") ?? null),
          context:
            labels.full.get(
              (row.type === "person" ? row.target : row.context) ?? "",
            ) ?? null,
          date: row.date,
        }))
        .filter((row) => row.path != null && row.name !== "")
        .slice(0, input.limit);
    }),
};
