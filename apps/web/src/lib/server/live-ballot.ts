import type { LiveRule, LiveUnopposed } from "@ea/analytics/live";
import { sql } from "drizzle-orm";
import type { Geography } from "#lib/live/standing.js";
import { rule } from "#lib/measure.js";
import { remember } from "#lib/server/cache.js";
import { liveDb as db } from "#lib/server/db/index.js";
import { geography, portraits } from "#lib/server/live-board.js";
import { night } from "#lib/server/live-page.js";
import { unopposed } from "#lib/server/live-unopposed.js";
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
  person: string | null;
  photo: string | null;
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
  sitting: Array<{ party: string; caucus: string }>;
  area: string | null;
  isMeasure: boolean;
  isSpecial: boolean;
  rule?: LiveRule | null;
  unopposed?: LiveUnopposed | null;
  options: Array<BallotOption>;
};

export type BallotParty = { name: string; short: string; color: string | null };

export type BallotHoldover = { party: string; caucus: string; seats: number };

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
  if (row.body === "lower_house" || row.body === "unicameral") {
    return "lower";
  }
  return row.body == null ? null : "statewide";
}

function clean(title: string, prefix: string | null) {
  let found = title.replace(/^\d{4} /u, "");
  if (prefix != null && found.endsWith(` in ${prefix}`)) {
    found = found.slice(0, -` in ${prefix}`.length);
  }
  found = found
    .replace(/ (special )?election$/u, (_, special?: string) =>
      special == null ? "" : " special",
    )
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
  const [named, faces] = await Promise.all([
    names(people, date),
    portraits(people),
  ]);
  return found.rows.map((row) => ({
    id: row.id,
    contest: row.contest,
    person: row.person,
    photo: row.person == null ? null : (faces.get(row.person) ?? null),
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
    codes: Array<string> | null;
    rules: unknown;
  }>(sql`
    select c.id, s.path, c.district_area_id as area,
      (select array_agg(ac.code) from area_code ac where ac.area_id = c.district_area_id) as codes, c.kind::text as kind, o.kind::text as office, b.kind::text as body, bp.kind::text as scope,
      coalesce(c.body_id, o.body_id) as chamber, c.magnitude as seats, prior.party as held,
      case when c.kind = 'ballot_question' then es.rules || coalesce(c.rules_override, '{}') end as rules
    from contest c
    left join electoral_system es on es.id = c.electoral_system_id
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
  const [options, lone, titles, bodyNames, seated] = await Promise.all([
    choices(ids, date),
    unopposed(ids),
    names(ids, date),
    names(bodies, date),
    db.execute<{
      body: string;
      person: string;
      since: string | null;
      codes: Array<string> | null;
      party: string | null;
      caucus: string | null;
    }>(sql`
      select distinct on (t.body_id, t.person_id) t.body_id as body, t.person_id as person, t.from_date::text as since,
        (select array_agg(ac.code) from area_code ac where ac.area_id = coalesce(st.district_area_id, w.district_area_id)) as codes,
        coalesce(pb.parent_party_id, pa.party) as party,
        case when gr.found then coalesce(gb.parent_party_id, gr.party_id) else coalesce(pb.parent_party_id, pa.party) end as caucus
      from tenure t
      left join seat st on st.id = t.seat_id
      left join contest w on w.id = t.contest_id
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = t.id and ta.kind = 'party' and ta.valid @> ${date}::date order by ta.party_id is null limit 1
      ) sp on true
      cross join lateral (
        select case when sp.found then sp.party_id else (
          select o.party_id from candidacy ca join contest_option o on o.id = ca.option_id where ca.person_id = t.person_id and o.party_id is not null
          order by (o.contest_id = t.contest_id) desc nulls last, o.contest_id desc limit 1
        ) end as party
      ) pa
      left join lateral (
        select ta.party_id, true as found from tenure_affiliation ta
        where ta.tenure_id = t.id and ta.kind = 'parliamentary_group' and ta.valid @> ${date}::date limit 1
      ) gr on true
      left join party_affiliation pb on pb.party_id = pa.party and pb.kind = 'regional_branch' and pb.valid @> ${date}::date
      left join party_affiliation gb on gb.party_id = gr.party_id and gb.kind = 'regional_branch' and gb.valid @> ${date}::date
      where t.body_id = any(${sql.param(bodies)}::uuid[])
        and (t.from_date is null or t.from_date <= ${date}::date) and (t.to_date is null or t.to_date > ${date}::date)
      order by t.body_id, t.person_id, t.from_date desc nulls last
    `),
  ]);
  const byContest = Map.groupBy(options, (option) => option.contest);
  const sitters = seated.rows
    .map((row) => ({
      ...row,
      party: row.party ?? "",
      caucus: row.caucus ?? "",
      codes: new Set(row.codes),
    }))
    .toSorted((a, b) => (b.since ?? "").localeCompare(a.since ?? ""));
  const placed = new Set<string>();
  const sittingOf = new Map(
    rows.flatMap((row) => {
      if ((row.tier !== "upper" && row.tier !== "lower") || row.codes == null) {
        return [];
      }
      const codes = row.codes;
      const found = sitters
        .filter(
          (sitter) =>
            sitter.body === row.chamber &&
            !placed.has(sitter.person) &&
            codes.some((code) => sitter.codes.has(code)),
        )
        .slice(0, row.seats ?? 1);
      for (const sitter of found) {
        placed.add(sitter.person);
      }
      return [[row.id, found] as const];
    }),
  );
  const kept = Map.groupBy(
    sitters.filter((sitter) => !placed.has(sitter.person)),
    (sitter) => sitter.body,
  );
  const parties = await partiesOf(
    [
      ...options.flatMap((option) =>
        option.party == null ? [] : [{ family: option.party, raw: option.raw }],
      ),
      ...rows.flatMap((row) =>
        row.held == null ? [] : [{ family: row.held, raw: null }],
      ),
      ...sitters.flatMap((sitter) =>
        [sitter.party, sitter.caucus].flatMap((family) =>
          family === "" ? [] : [{ family, raw: null }],
        ),
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
    const sitting = sittingOf.get(row.id) ?? [];
    return {
      tier: row.tier,
      chamber: row.chamber,
      contest: {
        id: row.id,
        slug: row.path,
        title: clean(titles.full.get(row.id) ?? seat, stateName),
        seat: district,
        seats: row.seats ?? 1,
        held:
          sitting[0] == null
            ? row.held
            : sitting[0].party === ""
              ? null
              : sitting[0].party,
        sitting: sitting.map((sitter) => ({
          party: sitter.party,
          caucus: sitter.caucus,
        })),
        area: district == null ? null : row.area,
        isMeasure: row.tier === "measures",
        isSpecial: seat.includes("special"),
        rule: rule(row.rules),
        unopposed: lone.get(row.id) ?? null,
        options: own.map((option) => ({
          id: option.id,
          name: option.name,
          person: option.person,
          photo: option.photo,
          party: option.party,
          isIncumbent: option.isIncumbent,
          isWriteIn: option.isWriteIn,
        })),
      } satisfies BallotContest,
    };
  });
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
        holdovers: [
          ...Map.groupBy(
            kept.get(body) ?? [],
            (sitter) => `${sitter.caucus}:${sitter.party}`,
          ).values(),
        ].map((group): BallotHoldover => ({
          party: group[0]?.party ?? "",
          caucus: group[0]?.caucus ?? "",
          seats: group.length,
        })),
      };
    }),
    caucuses: Object.fromEntries(
      sitters.flatMap((sitter) =>
        sitter.caucus === sitter.party ? [] : [[sitter.person, sitter.caucus]],
      ),
    ),
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
  return remember(`live:ballot-locals:v4:${path}`, async () => {
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
      rules: unknown;
    }>(sql`
      ${tree(election.id)}
      select c.id, s.path, c.kind::text as kind, c.magnitude as seats, e.polity_id as polity, p.kind::text as scope,
        case when c.kind = 'ballot_question' then es.rules || coalesce(c.rules_override, '{}') end as rules
      from tree t join election e on e.id = t.id join polity p on p.id = e.polity_id
      join contest c on c.election_id = e.id
      left join electoral_system es on es.id = c.electoral_system_id
      join slug s on s.entity_id = c.id and s.is_current
      where t.depth > 0
      order by s.path
    `);
    const ids = found.rows.map((row) => row.id);
    const polities = [...new Set(found.rows.map((row) => row.polity))];
    const [options, lone, titles, places] = await Promise.all([
      choices(ids, election.date),
      unopposed(ids),
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
                sitting: [],
                area: null,
                isMeasure: row.kind === "ballot_question",
                isSpecial: /special|unexpired|short-term/u.test(seat),
                rule: rule(row.rules),
                unopposed: lone.get(row.id) ?? null,
                options: (byContest.get(row.id) ?? []).map((option) => ({
                  id: option.id,
                  name: option.name,
                  person: option.person,
                  photo: option.photo,
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

export async function liveBallot(path: string) {
  if (path.split("/").length !== 3 || !path.startsWith("us/")) {
    return null;
  }
  const { election } = await night(path);
  if (election.slug !== path) {
    return null;
  }
  const state = election.slug.split("/", 2)[1] ?? "";
  return remember(`live:ballot:v12:${election.slug}`, async () => {
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
    const drawn = await Promise.all(
      [...groups].map(async ([key, list]) => {
        const ids = [
          ...new Set(list.flatMap((entry) => entry.contest.area ?? [])),
        ];
        return [
          key,
          await geography(ids, election.date, ids, false, true),
        ] as const;
      }),
    );
    const maps: Record<string, Geography | null> = Object.fromEntries(drawn);
    return { state, name: stateName, ...found, maps, locals };
  });
}

export type Ballot = NonNullable<Awaited<ReturnType<typeof liveBallot>>>;
