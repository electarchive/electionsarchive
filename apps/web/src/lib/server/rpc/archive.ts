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
        name: v.string(),
        path: v.nullable(v.string()),
        contest: v.boolean(),
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
      ${CYCLE} as year, e.status::text as status,
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
    .toSorted((a, b) => a.name.localeCompare(b.name));
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
    with fetched as (
      select r.result_set_k as set, r.area_k as area, r.option_k as option, r.measure, r.count_round,
        r.value::float8 as value, r.exact,
        min(r.count_round) over (partition by r.result_set_k, r.area_k, r.measure) as first
      from unnest(${sql.param(picked.rows.map((row) => row.k))}::int[], ${sql.param(picked.rows.map((row) => row.area))}::int[]) as p(set, area)
      join result_rollup r on r.result_set_k = p.set and r.area_k = p.area
      where (r.measure = any(${COUNTED}) or r.option_k = 0 and r.measure in (2, 4))
    ),
    mains as (
      select area, (${COUNTED})[min(array_position(${COUNTED}, measure))] as main from fetched group by area
    ),
    kept as (
      select distinct f.set, f.area from fetched f join mains m on m.area = f.area and m.main = f.measure
    )
    select r.set, r.area, r.option, x.role, m.main, r.value, r.exact
    from fetched r
    join mains m on m.area = r.area
    join kept k on k.set = r.set and k.area = r.area
    cross join lateral (
      select case
        when r.measure = m.main then 'votes'
        when r.measure = 4 and m.main = 1 then 'valid'
        when r.measure = 2 and m.main = 11 then 'appointed'
      end as role
    ) x
    where r.count_round = r.first and x.role is not null
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
  const backing = await db.execute<{ party: string; person: string }>(sql`
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
  `);
  const entities = [
    ...new Set([
      ...options.rows.flatMap((row) =>
        [row.party, row.person, row.alliance].filter((id) => id != null),
      ),
      ...backing.rows.map((row) => row.person),
    ]),
  ];
  const parties = [
    ...new Set(options.rows.map((row) => row.party).filter((id) => id != null)),
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
  separate(
    color,
    options.rows.map((row) => [row.party, tallies.get(row.k) ?? 0] as const),
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
            color: tints.get(row.k) ?? color.get(row.party ?? "") ?? null,
            lean: lean.get(row.party ?? "") ?? null,
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
                a.place.name.localeCompare(b.place.name) ||
                (at.get(a.place.id) ?? 0) - (at.get(b.place.id) ?? 0),
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
      coalesce(pm.into_id, o.party_id) as party, coalesce(x.seats, 1) as seats,
      coalesce(m.into_id, y.person_id) as person, y.list_position as position,
      coalesce(y.seated, false) as "isSeated", coalesce(y.elected, false) as "isElected"
    from contest c
    left join slug s on s.entity_id = c.id and s.is_current
    join result_set rs on rs.contest_id = c.id and rs.is_default
    join outcome x on x.result_set_id = rs.id and x.candidacy_id is null and (x.seats > 0 or (x.seats is null and x.kind in ('elected', 'won')))
    join contest_option o on o.id = x.option_id
    left join entity_merge pm on pm.entity_id = o.party_id
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
  return {
    levels: levels.map((entry) => ({
      code: entry.code,
      noun: entry.noun,
      count: entry.count,
    })),
    level: level.code,
    ...(await tabulate(
      [contest.root],
      rows,
      contest.date,
      chosen,
      lineage,
      match,
      grouping,
    )),
  };
}

async function tabulate(
  roots: Array<string>,
  rows: Array<{
    k: number;
    name: string;
    path: string | null;
    isContest: boolean;
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
      Array.from({ length: Math.max(0, ...tier) + 1 }, (_, n) => {
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
      };
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
      person: item.key.startsWith("person:") ? item.person : null,
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
        name: row.name,
        path: row.path,
        contest: row.isContest,
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
          parent: v.nullable(v.string()),
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
      const [parts, ancestors, cited] = await Promise.all([
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
          parent: isRoot ? null : (trail.at(-2)?.name ?? null),
          trail,
        },
        series: [],
        notes: notes.rows,
        children: childIds
          .map((id) => ({
            name: labels.full.get(id) ?? "",
            path: slugs.get(id) ?? null,
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
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
      }>(sql`
        with recursive anc(id) as (
          select ${area.id}::uuid
          union
          select p.parent_id from area_parent p join anc on p.area_id = anc.id
        ),
        roots as (
          select c.id, case when c.kind = 'ballot_question' then c.id::text
            else concat_ws(':', c.office_id, c.body_id, c.kind, c.round_number, c.party_scope_id) end as series,
            e.id as election, e.kind = 'indirect' as indirect, e.date_from, ${CYCLE} as year, e.status
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
        select r.id, r.series, r.election, r.indirect, r.date_from::text as date, r.year, r.status::text as status
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
        .toSorted((a, b) => (a.date ?? "").localeCompare(b.date ?? ""))
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
          : load([chosen.id], [area.k], chosen.date ?? "infinity", []),
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
            },
          ];
        }),
      );
      return drawn.flat();
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
            a.name.localeCompare(b.name),
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

async function races(election: string, polity: string, date: string | null) {
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
      area: string | null;
      k: number | null;
      scheme: string | null;
      noun: string | null;
    }>(sql`
      with recursive tree(id, stage) as (
        select ${election}::uuid, false
        union all
        select k.id, tree.stage or k.kind = 'indirect' from election k join tree on k.parent_id = tree.id
      )
      select c.id, c.kind::text as kind,
        case when c.kind = 'ballot_question' then c.id::text
          else concat_ws(':', c.office_id, c.body_id, c.kind, case when e.kind <> 'general' then e.date_from end, c.party_scope_id, tree.stage) end as series,
        coalesce(c.body_id, c.office_id) as owner, c.office_id as office, c.election_id as election,
        e.date_from::text as day, c.round_number as round,
        c.kind = 'office' as "isOffice", tree.stage as "isStage", z.seats as size,
        c.term is distinct from 'remainder' and coalesce(st.voting, true) as "isCounted",
        e.kind = 'runoff' as "isRunoff", c.previous_round_id as previous, c.party_scope_id as party,
        array(select n.id from contest n join election ne on ne.id = n.election_id where n.previous_round_id = c.id and ne.kind = 'runoff') as runoffs,
        a.id as area, a.k, a.scheme_code as scheme, sc.noun
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
      where c.allocation_parent_id is null
    `),
    db.execute<{ id: string; k: number }>(sql`
      select a.id, a.k from polity_territory pt join area a on a.id = pt.area_id
      where pt.polity_id = ${polity}::uuid and pt.valid @> ${day}::date limit 1
    `),
  ]);
  const nation = territory.rows[0];
  const groups = Map.groupBy(found.rows, (row) => row.series)
    .values()
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
      const [totals, grid, table] = await Promise.all([
        load(roots, [place.k], day, counted.length > 0 ? counted : roots),
        single == null
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
        single != null || first == null || districts.length < 2
          ? null
          : tabulate(
              roots,
              districts.map((row) => ({
                k: row.k ?? 0,
                name: labels.full.get(row.area ?? "") ?? "",
                path: slugs.get(row.id) ?? null,
                isContest: true,
                owner: row.id,
              })),
              date,
              null,
              lineage,
              "party",
            ),
      ]);
      const here = totals.get(place.k);
      const rows = (
        here == null ? [] : compare(here.side, null, lineage, "party")
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
          const lead = members.find((row) => row.key === key) ?? members[0];
          const party = key.startsWith("party:") ? key.slice(6) : null;
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
      return {
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
    .map(({ isStage, office, election, day, ...race }) => {
      const stage = stages.find((entry) => entry.office === office);
      const rows = (
        stage == null && race.measure === "electoral_votes" ? race : stage
      )?.results;
      return {
        ...race,
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
    })
    .toSorted((a, b) => b.valid - a.valid);
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
          .toSorted((a, b) => a.name.localeCompare(b.name)),
        elections: electionRows.rows
          .map((row) => ({
            name: labels.full.get(row.id) ?? "",
            place: labels.full.get(row.polity ?? "") ?? null,
            noun: row.polity == null ? null : row.noun,
            path: slugs.get(row.id) ?? null,
            date: row.date,
            isStage: row.kind === "runoff" || row.kind === "indirect",
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
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
        }),
      ),
    )
    .handler(async () => {
      const result = await db.execute<{
        id: string;
        kind: string;
        iso_code: string | null;
        elections: number;
      }>(sql`
        select p.id, p.kind::text, p.iso_code, (select count(*)::int from election e where e.polity_id = p.id) as elections
        from polity p
      `);
      const ids = result.rows.map((row) => row.id);
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      return result.rows
        .map((row) => ({
          id: row.id,
          kind: row.kind,
          isoCode: row.iso_code,
          name: labels.full.get(row.id) ?? "",
          path: slugs.get(row.id) ?? null,
          elections: row.elections,
        }))
        .toSorted((a, b) => a.name.localeCompare(b.name));
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
        polities: v.array(Link),
        elections: v.array(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
            status: v.string(),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const [found, children, electionRows, crumbs] = await Promise.all([
        db.execute<{ kind: string }>(
          sql`select kind::text from polity where id = ${input.id}::uuid`,
        ),
        db.execute<{ id: string }>(sql`
          select distinct polity_id as id from polity_parent where parent_id = ${input.id}::uuid
        `),
        db.execute<{ id: string; date: string | null; status: string }>(sql`
          select e.id, e.date_from::text as date, e.status::text as status from election e
          where e.polity_id = ${input.id}::uuid
            and not exists (select from election p where p.id = e.parent_id and p.polity_id = e.polity_id)
          order by e.date_from desc nulls last
        `),
        breadcrumbs(input.id),
      ]);
      const polity = found.rows[0];
      if (polity == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const ids = [
        input.id,
        ...children.rows.map((row) => row.id),
        ...electionRows.rows.map((row) => row.id),
      ];
      const [labels, slugs] = await Promise.all([names(ids), paths(ids)]);
      return {
        name: labels.full.get(input.id) ?? "",
        kind: polity.kind,
        breadcrumbs: crumbs.slice(0, -1),
        polities: children.rows
          .map((row) => ({
            name: labels.full.get(row.id) ?? "",
            path: slugs.get(row.id) ?? null,
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
        elections: electionRows.rows.map((row) => ({
          name: labels.full.get(row.id) ?? "",
          path: slugs.get(row.id) ?? null,
          date: row.date,
          status: row.status,
        })),
      };
    }),
};

const PartyRef = v.object({
  id: v.string(),
  label: v.string(),
  short: v.nullable(v.string()),
  color: v.nullable(v.string()),
});

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

export const people = {
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
          select o.id as option, o.contest_id, coalesce(pm.into_id, cd.member_of_party_id, o.party_id) as party
          from candidacy cd join contest_option o on o.id = cd.option_id
          left join entity_merge pm on pm.entity_id = coalesce(cd.member_of_party_id, o.party_id)
          where cd.person_id in (select id from ids) and cd.role = 'head'
        ),
        lines as materialized (
          select o.k from contest_option o
          where o.id in (select option from mine) or o.parent_option_id in (select option from mine)
        ),
        up(id, parent) as (
          select c.id, c.allocation_parent_id from contest c where c.id in (select contest_id from mine)
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
            from mine m join outcome x on x.option_id = m.option and x.candidacy_id is null join result_set rs on rs.id = x.result_set_id and rs.is_default
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
      const [entities, slugs] = await Promise.all([
        refs([
          party.id,
          ...polity,
          ...relations.rows.map((row) => row.other),
          ...parents.rows.map((row) => row.parent),
        ]),
        paths(polity),
      ]);
      const self = entities.get(party.id);
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
          return other == null
            ? []
            : [
                {
                  kind: row.kind,
                  outgoing: row.outgoing,
                  date: row.date,
                  party: other,
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
