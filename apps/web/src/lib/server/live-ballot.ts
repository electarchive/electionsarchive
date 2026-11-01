import { sql } from "drizzle-orm";
import type { Geography } from "#lib/live/standing.js";
import type { LivePage } from "#lib/server/live-page.js";
import { remember } from "#lib/server/cache.js";
import { db } from "#lib/server/db/index.js";
import { geography } from "#lib/server/live-board.js";
import { names } from "#lib/server/rpc/archive.js";

export type BallotTier =
  | "senate"
  | "governor"
  | "statewide"
  | "house"
  | "upper"
  | "lower"
  | "courts"
  | "measures";

export type BallotLocalKind =
  | "county"
  | "city"
  | "school"
  | "special"
  | "judicial"
  | "other";

export type BallotOption = {
  id: string;
  name: string;
  party: string | null;
  isIncumbent: boolean;
  isWriteIn: boolean;
};

export type BallotContest = {
  id: string;
  slug: string;
  title: string;
  seat: string | null;
  seats: number;
  held: string | null;
  area: string | null;
  isMeasure: boolean;
  isSpecial: boolean;
  options: Array<BallotOption>;
};

export type BallotParty = { name: string; short: string; color: string | null };

const JUDICIAL = /justice|judge|court|magistrate/u;

const KINDS: Record<string, BallotLocalKind> = {
  county: "county",
  municipality: "city",
  submunicipal: "city",
  school_district: "school",
  special_district: "special",
  judicial_district: "judicial",
};

function tierOf(row: {
  path: string;
  kind: string;
  office: string | null;
  body: string | null;
  scope: string | null;
}): BallotTier | null {
  const seat = row.path.split("/").at(-1) ?? "";
  if (row.kind === "ballot_question") {
    return "measures";
  }
  if (row.scope === "sovereign_state") {
    if (row.body === "upper_house") {
      return "senate";
    }
    return row.body === "lower_house" ? "house" : null;
  }
  if (row.office === "governor") {
    return "governor";
  }
  if (row.office != null) {
    return JUDICIAL.test(seat) ? "courts" : "statewide";
  }
  if (row.body === "upper_house") {
    return "upper";
  }
  return row.body === "lower_house" || row.body === "unicameral"
    ? "lower"
    : null;
}

function clean(title: string, prefix: string | null) {
  let found = title
    .replace(/^\d{4} /u, "")
    .replace(/ (?:special )?election$/u, "")
    .replace(/ gubernatorial$/u, " governor");
  for (const glue of [": ", " "]) {
    if (prefix != null && found.startsWith(`${prefix}${glue}`)) {
      found = found.slice(prefix.length + glue.length);
      break;
    }
  }
  return found.replace(/^\p{Ll}/u, (letter) => letter.toUpperCase());
}

async function choices(contests: Array<string>, date: string) {
  const found = await db.execute<{
    id: string;
    contest: string;
    kind: string;
    label: string | null;
    person: string | null;
    ballot: string | null;
    incumbent: boolean;
    party: string | null;
    family: string | null;
  }>(sql`
    select o.id, o.contest_id as contest, o.kind::text as kind, o.ballot_label as label, ca.person_id as person,
      ca.ballot_name as ballot, coalesce(ca.is_incumbent, false) as incumbent, p.party,
      coalesce(a.parent_party_id, p.party) as family
    from contest_option o
    left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
    cross join lateral (
      select coalesce(o.party_id, (
        select x.party_id from contest_option x where x.parent_option_id = o.id and x.party_id is not null order by x.k limit 1
      )) as party
    ) p
    left join party_affiliation a on a.party_id = p.party and a.kind = 'regional_branch' and a.valid @> ${date}::date
    where o.contest_id = any(${sql.param(contests)}::uuid[]) and o.parent_option_id is null
    order by o.contest_id, o.ballot_position nulls last, o.k
  `);
  const people = found.rows.flatMap((row) =>
    row.person == null ? [] : [row.person],
  );
  const named = await names(people, date);
  return found.rows.map((row) => ({
    id: row.id,
    contest: row.contest,
    name:
      (row.person == null ? null : named.full.get(row.person)) ??
      row.ballot ??
      row.label ??
      "",
    party: row.kind === "write_in" ? null : row.family,
    raw: row.party,
    isIncumbent: row.incumbent,
    isWriteIn: row.kind === "write_in",
  }));
}

function familiesOf(
  options: Array<{ party: string | null; raw: string | null }>,
) {
  return Object.fromEntries(
    options.flatMap((option) =>
      option.raw == null || option.party == null || option.raw === option.party
        ? []
        : [[option.raw, option.party] as const],
    ),
  );
}

async function partiesOf(
  pairs: Array<{ family: string; raw: string | null }>,
  date: string,
) {
  const families = [...new Set(pairs.map((pair) => pair.family))];
  const raw = [
    ...new Set(pairs.flatMap((pair) => (pair.raw == null ? [] : [pair.raw]))),
  ];
  const [named, colors] = await Promise.all([
    names(families, date),
    db.execute<{ party: string; color: string }>(sql`
      select party_id as party, color from party_color
      where valid @> ${date}::date and party_id = any(${sql.param([...families, ...raw])}::uuid[])
    `),
  ]);
  const tint = new Map(colors.rows.map((row) => [row.party, row.color]));
  const fallback = new Map(
    pairs.flatMap((pair) =>
      pair.raw == null || tint.get(pair.raw) == null
        ? []
        : [[pair.family, tint.get(pair.raw) ?? ""] as const],
    ),
  );
  return Object.fromEntries(
    families.map((family): [string, BallotParty] => [
      family,
      {
        name: named.full.get(family) ?? "",
        short: named.short.get(family) ?? named.full.get(family) ?? "",
        color: tint.get(family) ?? fallback.get(family) ?? null,
      },
    ]),
  );
}

async function statewide(id: string, date: string, stateName: string) {
  const found = await db.execute<{
    id: string;
    path: string;
    kind: string;
    office: string | null;
    body: string | null;
    scope: string | null;
    chamber: string | null;
    seats: number | null;
    held: string | null;
    area: string | null;
  }>(sql`
    select c.id, s.path, c.district_area_id as area, c.kind::text as kind, o.kind::text as office, b.kind::text as body, bp.kind::text as scope,
      coalesce(c.body_id, o.body_id) as chamber, c.magnitude as seats, prior.party as held
    from contest c
    join slug s on s.entity_id = c.id and s.is_current
    left join office o on o.id = c.office_id
    left join body b on b.id = coalesce(c.body_id, o.body_id)
    left join polity bp on bp.id = b.polity_id
    left join lateral (
      select coalesce(a.parent_party_id, x.party_id) as party from (
        select p.id, p.election_id, p.party_scope_id from contest p where c.seat_id is not null and p.seat_id = c.seat_id
        union all
        select p.id, p.election_id, p.party_scope_id from contest p where c.seat_id is null and c.office_id is not null and p.office_id = c.office_id
        union all
        select p.id, p.election_id, p.party_scope_id from contest p
        where c.seat_id is null and c.office_id is null and c.district_area_id is not null
          and p.district_area_id = c.district_area_id and p.body_id = c.body_id
      ) p
      join election e on e.id = p.election_id
      join result_set rs on rs.contest_id = p.id and rs.is_default
      join outcome oc on oc.result_set_id = rs.id and oc.kind in ('elected', 'won')
      join contest_option x on x.id = oc.option_id
      left join party_affiliation a on a.party_id = x.party_id and a.kind = 'regional_branch' and a.valid @> ${date}::date
      where c.kind <> 'ballot_question' and p.id <> c.id and p.party_scope_id is null and e.date_from < ${date}::date
        and e.date_from > ${date}::date - 4400 and e.kind <> 'primary'
      order by e.date_from desc limit 1
    ) prior on true
    where c.election_id = ${id}::uuid
  `);
  const rows = found.rows.flatMap((row) => {
    const tier = tierOf(row);
    return tier == null ? [] : [{ ...row, tier }];
  });
  const ids = rows.map((row) => row.id);
  const bodies = [
    ...new Set(
      rows.flatMap((row) =>
        (row.tier === "upper" || row.tier === "lower") && row.chamber != null
          ? [row.chamber]
          : [],
      ),
    ),
  ];
  const [options, titles, bodyNames, holdovers] = await Promise.all([
    choices(ids, date),
    names(ids, date),
    names(bodies, date),
    db.execute<{ body: string; party: string | null; seats: number }>(sql`
      with up as (
        select c.body_id as body, c.district_area_id as area from contest c
        where c.election_id = ${id}::uuid and c.body_id = any(${sql.param(bodies)}::uuid[])
      ), recent as (
        select distinct on (p.body_id, p.district_area_id) p.body_id as body, p.district_area_id as area, p.id
        from contest p join election e on e.id = p.election_id
        where p.body_id = any(${sql.param(bodies)}::uuid[]) and p.district_area_id is not null and p.party_scope_id is null
          and e.kind <> 'primary' and e.date_from < ${date}::date and e.date_from > ${date}::date - 1500
        order by p.body_id, p.district_area_id, e.date_from desc
      )
      select r.body, coalesce(a.parent_party_id, x.party_id) as party, count(*)::int as seats
      from recent r
      join result_set rs on rs.contest_id = r.id and rs.is_default
      join outcome oc on oc.result_set_id = rs.id and oc.kind in ('elected', 'won')
      join contest_option x on x.id = oc.option_id
      left join party_affiliation a on a.party_id = x.party_id and a.kind = 'regional_branch' and a.valid @> ${date}::date
      where not exists (select from up where up.body = r.body and up.area = r.area)
      group by 1, 2
    `),
  ]);
  const byContest = Map.groupBy(options, (option) => option.contest);
  const parties = await partiesOf(
    [
      ...options.flatMap((option) =>
        option.party == null ? [] : [{ family: option.party, raw: option.raw }],
      ),
      ...rows.flatMap((row) =>
        row.held == null ? [] : [{ family: row.held, raw: null }],
      ),
      ...holdovers.rows.flatMap((row) =>
        row.party == null ? [] : [{ family: row.party, raw: null }],
      ),
    ],
    date,
  );
  const contests = rows.map((row) => {
    const seat = row.path.split("/").at(-1) ?? "";
    const district =
      row.tier === "house" || row.tier === "upper" || row.tier === "lower"
        ? (/-(\d+|at-large)(?:-special(?:-election)?)?$/u.exec(seat)?.[1] ??
          null)
        : null;
    const own = byContest.get(row.id) ?? [];
    return {
      tier: row.tier,
      chamber: row.chamber,
      contest: {
        id: row.id,
        slug: row.path,
        title: clean(titles.full.get(row.id) ?? seat, stateName),
        seat: district,
        seats: row.seats ?? 1,
        held: row.held,
        area: district == null ? null : row.area,
        isMeasure: row.tier === "measures",
        isSpecial: seat.includes("special"),
        options: own.map((option) => ({
          id: option.id,
          name: option.name,
          party: option.party,
          isIncumbent: option.isIncumbent,
          isWriteIn: option.isWriteIn,
        })),
      } satisfies BallotContest,
    };
  });
  const kept = Map.groupBy(holdovers.rows, (row) => row.body);
  return {
    contests,
    chambers: bodies.map((body) => {
      const tier =
        rows.find((row) => row.chamber === body)?.tier === "upper"
          ? ("upper" as const)
          : ("lower" as const);
      return {
        id: body,
        tier,
        name: bodyNames.full.get(body) ?? "",
        holdovers: Object.fromEntries(
          (kept.get(body) ?? []).map((row) => [row.party ?? "", row.seats]),
        ),
      };
    }),
    parties,
    families: familiesOf(options),
  };
}

function tree(id: string) {
  return sql`
    with recursive tree(id, depth) as (
      select ${id}::uuid, 0
      union all
      select e.id, t.depth + 1 from election e join tree t on e.parent_id = t.id where t.depth < 4
    )
  `;
}

async function tally(id: string) {
  const found = await db.execute<{
    kind: string;
    measures: number;
    n: number;
    places: number;
  }>(
    sql`
      ${tree(id)}
      select p.kind::text as kind, count(*) filter (where c.kind = 'ballot_question')::int as measures, count(*)::int as n, count(distinct e.polity_id)::int as places
      from tree t join election e on e.id = t.id join polity p on p.id = e.polity_id
      join contest c on c.election_id = e.id
      where t.depth > 0
      group by 1
    `,
  );
  let total = 0;
  let measures = 0;
  let places = 0;
  const kinds: Partial<Record<BallotLocalKind, number>> = {};
  for (const row of found.rows) {
    total += row.n;
    measures += row.measures;
    places += row.places;
    const kind = KINDS[row.kind] ?? "other";
    kinds[kind] = (kinds[kind] ?? 0) + row.n - row.measures;
  }
  return { total, measures, places, kinds };
}

export async function ballotLocals(path: string) {
  return remember(`live:ballot-locals:v2:${path}`, async () => {
    const root = await db.execute<{ id: string; date: string }>(sql`
      select e.id, e.date_from::text as date from slug s join election e on e.id = s.entity_id
      where s.path = ${path} and s.is_current
    `);
    const election = root.rows[0];
    if (election == null) {
      return null;
    }
    const found = await db.execute<{
      id: string;
      path: string;
      kind: string;
      seats: number | null;
      polity: string;
      scope: string;
    }>(sql`
      ${tree(election.id)}
      select c.id, s.path, c.kind::text as kind, c.magnitude as seats, e.polity_id as polity, p.kind::text as scope
      from tree t join election e on e.id = t.id join polity p on p.id = e.polity_id
      join contest c on c.election_id = e.id
      join slug s on s.entity_id = c.id and s.is_current
      where t.depth > 0
      order by s.path
    `);
    const ids = found.rows.map((row) => row.id);
    const polities = [...new Set(found.rows.map((row) => row.polity))];
    const [options, titles, places] = await Promise.all([
      choices(ids, election.date),
      names(ids, election.date),
      names(polities, election.date),
    ]);
    const byContest = Map.groupBy(options, (option) => option.contest);
    const parties = await partiesOf(
      options.flatMap((option) =>
        option.party == null ? [] : [{ family: option.party, raw: option.raw }],
      ),
      election.date,
    );
    const nested = path.split("/").length + 3;
    return {
      parties,
      families: familiesOf(options),
      places: [...Map.groupBy(found.rows, (row) => row.polity)].map(
        ([polity, rows]) => {
          const name = places.full.get(polity) ?? "";
          const parts = (rows[0]?.path ?? "").split("/");
          const within = parts.length >= nested ? (parts[2] ?? null) : null;
          return {
            id: polity,
            name,
            kind: KINDS[rows[0]?.scope ?? ""] ?? "other",
            within,
            contests: rows.map((row) => {
              const seat = row.path.split("/").at(-1) ?? "";
              return {
                id: row.id,
                slug: row.path,
                title: clean(titles.full.get(row.id) ?? seat, name),
                seat: null,
                seats: row.seats ?? 1,
                held: null,
                area: null,
                isMeasure: row.kind === "ballot_question",
                isSpecial: /special|unexpired|short-term/u.test(seat),
                options: (byContest.get(row.id) ?? []).map((option) => ({
                  id: option.id,
                  name: option.name,
                  party: option.party,
                  isIncumbent: option.isIncumbent,
                  isWriteIn: option.isWriteIn,
                })),
              } satisfies BallotContest;
            }),
          };
        },
      ),
    };
  });
}

export type BallotLocals = NonNullable<
  Awaited<ReturnType<typeof ballotLocals>>
>;

export async function liveBallot(page: LivePage) {
  const { election } = page;
  if (
    page.isRace ||
    page.place != null ||
    election.slug.split("/").length !== 3 ||
    !election.slug.startsWith("us/")
  ) {
    return null;
  }
  const state = election.slug.split("/", 2)[1] ?? "";
  return remember(`live:ballot:v6:${election.slug}`, async () => {
    const stateName = election.title
      .replace(/^\d{4} /u, "")
      .replace(/ general election$/u, "");
    const [found, locals] = await Promise.all([
      statewide(election.id, election.date, stateName),
      tally(election.id),
    ]);
    const groups = Map.groupBy(
      found.contests.filter((entry) => entry.contest.area != null),
      (entry) => (entry.tier === "house" ? "house" : (entry.chamber ?? "")),
    );
    const maps: Record<string, Geography | null> = {};
    for (const [key, list] of groups) {
      const ids = [
        ...new Set(list.flatMap((entry) => entry.contest.area ?? [])),
      ];
      maps[key] = await geography(ids, election.date, ids, false);
    }
    return { state, name: stateName, ...found, maps, locals };
  });
}

export type Ballot = NonNullable<Awaited<ReturnType<typeof liveBallot>>>;
