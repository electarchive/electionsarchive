import type { Option, Relation, Side } from "@ea/analytics";
import { compare } from "@ea/analytics";
import { openapi } from "@orpc/openapi";
import { ORPCError, os } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import { db } from "#lib/server/db/index.js";

const STAGES = sql.raw(
  "array['court_revised','recount','certified','official','preliminary','live','alternative']::stage[]",
);

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
  }),
});

const Cell = v.nullable(
  v.object({
    votes: v.number(),
    share: v.number(),
    outcome: v.nullable(v.string()),
    seats: v.nullable(v.number()),
  }),
);

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

async function load(
  root: string,
  areaK: number,
  date: string,
  outcomes: string | null,
) {
  const picked = await db.execute<{
    k: number;
    stage: string;
    status: string;
    completeness: number | null;
    is_default: boolean;
    kind: string;
    publisher: string;
    title: string;
    url: string | null;
    license: string | null;
  }>(sql`
    with recursive grp(id) as (
      select ${root}::uuid
      union all
      select c.id from contest c join grp on c.allocation_parent_id = grp.id
    ),
    picked as (
      select distinct on (rs.contest_id) rs.k, rs.stage, rs.status, rs.completeness, rs.is_default, rs.source_id
      from result_set rs join grp on grp.id = rs.contest_id
      where exists (
        select 1 from result_rollup r where r.result_set_k = rs.k and r.area_k = ${areaK} and r.measure = 1
      )
      order by rs.contest_id, rs.is_default desc, array_position(${STAGES}, rs.stage), rs.completeness desc nulls last
    )
    select p.k, p.stage::text, p.status::text, p.completeness::float8 as completeness, p.is_default,
      s.kind::text, s.publisher, s.title, s.url, s.license
    from picked p join source s on s.id = p.source_id
  `);
  if (picked.rows.length === 0) {
    return null;
  }
  const rollup = await db.execute<{
    set: number;
    option: number;
    channel: number;
    measure: number;
    round: number;
    value: number;
    exact: boolean;
  }>(sql`
    select result_set_k as set, option_k as option, channel, measure, count_round as round,
      value::float8 as value, exact
    from result_rollup
    where result_set_k = any(${sql.param(picked.rows.map((row) => row.k))}::int[])
      and area_k = ${areaK} and count_round <= 1 and measure in (1, 4)
  `);
  const totals = new Map<number, number>();
  const first = new Map<string, number>();
  for (const row of rollup.rows) {
    totals.set(row.set, Math.min(totals.get(row.set) ?? 1, row.channel));
    first.set(
      `${row.set}:${row.measure}`,
      Math.min(first.get(`${row.set}:${row.measure}`) ?? 1, row.round),
    );
  }
  const rows = rollup.rows.filter(
    (row) =>
      (totals.get(row.set) === 0) === (row.channel === 0) &&
      first.get(`${row.set}:${row.measure}`) === row.round,
  );
  const votes = new Map<number, number>();
  const valid = new Map<number, number>();
  const counted = new Map<number, number>();
  for (const row of rows) {
    if (row.measure === 1) {
      votes.set(row.option, (votes.get(row.option) ?? 0) + row.value);
      counted.set(row.set, (counted.get(row.set) ?? 0) + row.value);
    } else if (row.option === 0) {
      valid.set(row.set, (valid.get(row.set) ?? 0) + row.value);
    }
  }
  let total = 0;
  for (const set of picked.rows) {
    total += valid.get(set.k) ?? counted.get(set.k) ?? 0;
  }

  const options = await db.execute<{
    k: number;
    contest: string;
    kind: string;
    party: string | null;
    person: string | null;
    label: string | null;
    outcome: string | null;
    seats: number | null;
  }>(sql`
    select o.k, o.contest_id as contest, o.kind::text, coalesce(pm.into_id, o.party_id) as party, o.ballot_label as label,
      (select coalesce(m.into_id, cy.person_id) from candidacy cy left join entity_merge m on m.entity_id = cy.person_id
       where cy.option_id = o.id and cy.role = 'head' order by cy.id limit 1) as person,
      oc.kind::text as outcome, oc.seats
    from contest_option o
    left join entity_merge pm on pm.entity_id = o.party_id
    left join lateral (
      select (array_agg(x.kind order by array_position(array['elected', 'advanced', 'won', 'tied']::outcome_kind[], x.kind)))[1] as kind,
        max(x.seats) as seats
      from outcome x join result_set rs on rs.id = x.result_set_id
      where x.option_id = o.id and o.contest_id = ${outcomes}::uuid and rs.is_default
        and x.kind in ('elected', 'won', 'advanced', 'tied')
    ) oc on true
    where o.k = any(${sql.param(votes.keys().toArray())}::int[])
      or o.contest_id = ${outcomes}::uuid and oc.kind is not null
  `);
  const backing = await db.execute<{ party: string; person: string }>(sql`
    with recursive grp(id) as (
      select ${root}::uuid
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
        [row.party, row.person].filter((id) => id != null),
      ),
      ...backing.rows.map((row) => row.person),
    ]),
  ];
  const parties = [
    ...new Set(options.rows.map((row) => row.party).filter((id) => id != null)),
  ];
  const [labels, colors] = await Promise.all([
    names(entities, date),
    db.execute<{ party_id: string; color: string }>(sql`
      select party_id, color from party_color
      where party_id = any(${sql.param(parties)}::uuid[]) and valid @> ${date}::date
    `),
  ]);
  const color = new Map(colors.rows.map((row) => [row.party_id, row.color]));
  const side: Side = {
    valid: total,
    options: options.rows.map((row): Option => ({
      contest: row.contest,
      kind: row.kind,
      party: row.party,
      person: row.person,
      label:
        (row.person == null ? null : labels.full.get(row.person)) ??
        (row.party == null ? null : labels.full.get(row.party)) ??
        row.label ??
        row.kind,
      short:
        row.party == null
          ? null
          : (labels.short.get(row.party) ??
            (row.person == null ? null : labels.full.get(row.party)) ??
            null),
      color: row.party == null ? null : (color.get(row.party) ?? null),
      votes: votes.get(row.k) ?? 0,
      outcome: row.outcome,
      seats: row.seats,
    })),
    backing: backing.rows.map((row) => ({
      party: row.party,
      candidate: labels.full.get(row.person) ?? "",
    })),
  };
  return {
    side,
    exact: rows.every((row) => row.exact),
    sets: picked.rows.map((row) => ({
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
      },
    })),
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

const blank = {
  current: null,
  target: null,
  comparison: { label: null, notes: [] },
  rows: [],
};

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
    status: string;
  }>,
  contest: { series: string; date: string | null },
  vs: string | undefined,
) {
  if (vs === "none") {
    return { chosen: null, notes: [] };
  }
  if (vs == null || vs === "" || vs === "previous") {
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

export const contests = {
  get: os
    .meta(
      openapi({
        method: "GET",
        path: "/contests/{id}",
        summary: "Get a contest's results, compared with another election",
      }),
    )
    .input(
      v.object({
        id: v.pipe(v.string(), v.uuid()),
        vs: v.optional(v.string()),
        match: v.optional(v.picklist(["party", "person", "family"]), "party"),
      }),
    )
    .output(
      v.object({
        name: v.string(),
        path: v.nullable(v.string()),
        breadcrumbs: v.array(Link),
        election: v.object({
          name: v.string(),
          path: v.nullable(v.string()),
          date: v.nullable(v.string()),
          status: v.string(),
        }),
        area: v.string(),
        notes: v.array(v.object({ kind: v.string(), body: v.string() })),
        children: v.array(Link),
        sets: v.array(ResultSet),
        declared: v.nullable(
          v.object({
            kind: v.string(),
            rows: v.array(
              v.object({
                key: v.string(),
                label: v.string(),
                short: v.nullable(v.string()),
                color: v.nullable(v.string()),
                outcome: v.string(),
              }),
            ),
          }),
        ),
        current: v.nullable(
          v.object({
            valid: v.number(),
            exact: v.boolean(),
            sets: v.array(ResultSet),
          }),
        ),
        target: v.nullable(
          v.object({
            name: v.string(),
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
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
            series: v.boolean(),
            annulled: v.boolean(),
          }),
        ),
      }),
    )
    .handler(async ({ input }) => {
      const found = await db.execute<{
        id: string;
        series: string;
        root: string;
        election: string;
        polity: string;
        date: string | null;
        status: string;
        area: string | null;
        area_k: number | null;
      }>(sql`
        with recursive up(id, parent) as (
          select id, allocation_parent_id from contest where id = ${input.id}::uuid
          union all
          select c.id, c.allocation_parent_id from contest c join up on c.id = up.parent
        )
        select c.id, concat_ws(':', r.office_id, r.body_id, r.kind, r.round_number, r.party_scope_id) as series,
          r.id as root, e.id as election, e.polity_id as polity, e.date_from::text as date, e.status::text as status,
          a.id as area, a.k as area_k
        from contest c
        join up on up.parent is null
        join contest r on r.id = up.id
        join election e on e.id = c.election_id
        left join polity_territory pt on pt.polity_id = e.polity_id and pt.valid @> e.date_from
        left join area a on a.id = coalesce(c.district_area_id, pt.area_id)
        where c.id = ${input.id}::uuid
      `);
      const contest = found.rows[0];
      if (contest == null) {
        throw new ORPCError("NOT_FOUND");
      }
      const children = await db.execute<{ id: string }>(sql`
        select id from contest where allocation_parent_id = ${contest.id}::uuid
      `);
      const childIds = children.rows.map((row) => row.id);
      const [labels, slugs, crumbs, notes, sets, declared] = await Promise.all([
        names(
          [
            contest.id,
            contest.election,
            ...childIds,
            ...(contest.area == null ? [] : [contest.area]),
          ],
          contest.date,
        ),
        paths([contest.id, contest.election, ...childIds]),
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
        }>(sql`
          select rs.stage::text, rs.status::text, rs.completeness::float8 as completeness, rs.is_default,
            s.kind::text, s.publisher, s.title, s.url, s.license
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
             where cy.option_id = o.id and cy.role = 'head' order by cy.id limit 1) as person,
            x.kind::text as outcome
          from result_set rs
          join outcome x on x.result_set_id = rs.id
          join contest_option o on o.id = x.option_id
          left join entity_merge pm on pm.entity_id = o.party_id
          where rs.contest_id = ${contest.id}::uuid and rs.is_default and rs.kind <> 'counted'
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
          select party_id, color from party_color
          where party_id = any(${sql.param(declaredParties)}::uuid[]) and valid @> ${contest.date ?? "infinity"}::date
        `),
      ]);
      const declaredColor = new Map(
        declaredColors.rows.map((row) => [row.party_id, row.color]),
      );
      const page = {
        name: labels.full.get(contest.id) ?? "",
        path: slugs.get(contest.id) ?? null,
        breadcrumbs: crumbs,
        election: {
          name: labels.full.get(contest.election) ?? "",
          path: slugs.get(contest.election) ?? null,
          date: contest.date,
          status: contest.status,
        },
        area: contest.area == null ? "" : (labels.full.get(contest.area) ?? ""),
        notes: notes.rows,
        children: childIds
          .map((id) => ({
            name: labels.full.get(id) ?? "",
            path: slugs.get(id) ?? null,
          }))
          .toSorted((a, b) => a.name.localeCompare(b.name)),
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
          },
        })),
        declared:
          declared.rows[0] == null
            ? null
            : {
                kind: declared.rows[0].kind,
                rows: declared.rows.map((row) => ({
                  key: row.key,
                  label:
                    (row.person == null
                      ? null
                      : declaredLabels.full.get(row.person)) ??
                    (row.party == null
                      ? null
                      : declaredLabels.full.get(row.party)) ??
                    row.label ??
                    "",
                  short:
                    row.party == null
                      ? null
                      : (declaredLabels.short.get(row.party) ??
                        declaredLabels.full.get(row.party) ??
                        null),
                  color:
                    row.party == null
                      ? null
                      : (declaredColor.get(row.party) ?? null),
                  outcome: row.outcome,
                })),
              },
      };
      if (contest.area == null || contest.area_k == null) {
        return { ...page, ...blank, targets: [] };
      }

      const covering = await db.execute<{
        id: string;
        series: string;
        election: string;
        date: string | null;
        status: string;
      }>(sql`
        with recursive anc(id) as (
          select ${contest.area}::uuid
          union
          select p.parent_id from area_parent p join anc on p.area_id = anc.id
        ),
        roots as (
          select c.id, concat_ws(':', c.office_id, c.body_id, c.kind, c.round_number, c.party_scope_id) as series,
            e.id as election, e.date_from, e.status
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
        select r.id, r.series, r.election, r.date_from::text as date, r.status::text as status
        from roots r
        where r.id <> ${contest.root}::uuid and exists (
          select 1 from grp join result_set rs on rs.contest_id = grp.id
          where grp.root = r.id and exists (
            select 1 from result_rollup x where x.result_set_k = rs.k and x.area_k = ${contest.area_k} and x.measure = 1
          )
        )
        order by r.date_from desc nulls last, r.id
      `);
      const rootIds = covering.rows.map((row) => row.id);
      const [rootLabels, rootSlugs] = await Promise.all([
        names(rootIds),
        paths(rootIds),
      ]);
      const targets = covering.rows.map((row) => ({
        value: rootSlugs.get(row.id) ?? row.id,
        label: rootLabels.full.get(row.id) ?? "",
        detail: row.date,
        series: row.series === contest.series,
        annulled: row.status === "annulled",
      }));

      const { chosen, notes: picked } = await pick(
        covering.rows,
        contest,
        input.vs,
      );
      const [current, earlier] = await Promise.all([
        load(
          contest.root,
          contest.area_k,
          contest.date ?? "infinity",
          contest.id,
        ),
        chosen == null
          ? null
          : load(chosen.id, contest.area_k, chosen.date ?? "infinity", null),
      ]);
      if (current == null) {
        return { ...page, ...blank, targets };
      }
      const notesOut: Array<Note> = [
        ...picked,
        ...caveats("current", current.sets),
        ...(earlier == null ? [] : caveats("target", earlier.sets)),
      ];
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
      const target =
        chosen == null || earlier == null
          ? null
          : {
              name: rootLabels.full.get(chosen.id) ?? "",
              path: rootSlugs.get(chosen.id) ?? null,
              date: chosen.date,
              status: chosen.status,
              valid: earlier.side.valid,
              exact: earlier.exact,
              sets: earlier.sets,
            };
      return {
        ...page,
        current: {
          valid: current.side.valid,
          exact: current.exact,
          sets: current.sets,
        },
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
        rows: compare(
          current.side,
          earlier?.side ?? null,
          lineage,
          input.match,
        ),
        targets,
      };
    }),
};

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
            path: v.nullable(v.string()),
            date: v.nullable(v.string()),
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
      const [contestRows, electionRows, notes, crumbs] = await Promise.all([
        db.execute<{ id: string; children: number }>(sql`
          select c.id, (select count(*)::int from contest x where x.allocation_parent_id = c.id) as children
          from contest c where c.election_id = ${input.id}::uuid and c.allocation_parent_id is null
        `),
        db.execute<{ id: string; date: string | null }>(sql`
          select id, date_from::text as date from election where parent_id = ${input.id}::uuid
        `),
        db.execute<{ kind: string; body: string }>(sql`
          select kind::text, body_md as body from note where entity_id = ${input.id}::uuid
        `),
        breadcrumbs(election.polity),
      ]);
      const ids = [
        input.id,
        ...contestRows.rows.map((row) => row.id),
        ...electionRows.rows.map((row) => row.id),
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
            path: slugs.get(row.id) ?? null,
            date: row.date,
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
          select id, date_from::text as date, status::text as status from election
          where polity_id = ${input.id}::uuid
          order by date_from desc nulls last
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
