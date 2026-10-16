import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { router } from "#lib/server/rpc/router.js";

async function found<T>(promise: Promise<T>) {
  try {
    return await promise;
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
}

async function draw() {
  const result = await db.execute<{
    id: string;
    path: string;
    covered: boolean;
    box: string;
    inset: string;
  }>(
    sql`
      with shapes as (
        select distinct on (pt.polity_id) pt.polity_id as id, ag.geom
        from polity_territory pt join polity y on y.id = pt.polity_id
        join area_geometry ag on ag.area_id = pt.area_id
        where y.kind = 'sovereign_state' and upper_inf(pt.valid)
        order by pt.polity_id, upper(ag.valid) desc nulls first, lower(ag.valid) desc nulls last
      ),
      context as (
        select distinct on (a.id) a.id, ag.geom
        from area a join area_geometry ag on ag.area_id = a.id
        where a.scheme_code = 'eu.member_state'
        order by a.id, upper(ag.valid) desc nulls first
      ),
      projected as (
        select id, ST_Scale(ST_Transform(geom, 8857), 0.001, 0.001) as geom, true as covered from shapes
        union all
        select id, ST_Scale(ST_Transform(geom, 8857), 0.001, 0.001), false from context
      ),
      cleaned as (
        select id, covered, ST_SimplifyPreserveTopology(ST_Collect(d.geom), 12) as geom
        from projected cross join lateral ST_Dump(projected.geom) d
        where ST_Area(d.geom) > 600
        group by id, covered
      ),
      bounds as (
        select ST_Extent(geom) as box,
          ST_Scale(ST_Transform(ST_Segmentize(ST_MakeEnvelope(-11, 35, 45, 61, 4326), 1), 8857), 0.001, 0.001) as europe
        from cleaned
      )
      select c.id, ST_AsSVG(c.geom, 0, 0) as path, c.covered,
        concat_ws(' ', floor(ST_XMin(b.box)) - 40, -ceil(ST_YMax(b.box)) - 40,
          ceil(ST_XMax(b.box) - ST_XMin(b.box)) + 80, ceil(ST_YMax(b.box) - ST_YMin(b.box)) + 80) as box,
        concat_ws(' ', floor(ST_XMin(b.europe)), -ceil(ST_YMax(b.europe)),
          ceil(ST_XMax(b.europe) - ST_XMin(b.europe)), ceil(ST_YMax(b.europe) - ST_YMin(b.europe))) as inset
      from cleaned c cross join bounds b
      order by c.covered, c.id
    `,
  );
  return {
    box: result.rows[0]?.box ?? "0 0 1 1",
    inset: result.rows[0]?.inset ?? "0 0 1 1",
    shapes: result.rows.map((row) => ({
      id: row.id,
      path: row.path,
      isCovered: row.covered,
    })),
  };
}

const atlas = new Map<string, ReturnType<typeof draw>>();

export async function home() {
  const drawn = atlas.get("world") ?? draw();
  atlas.set("world", drawn);
  const [polities, shapes, dated] = await Promise.all([
    call(router.polities.list, undefined),
    drawn,
    db.execute<{
      id: string;
      name: string | null;
      path: string;
      date: string;
      kind: string;
      polity: string;
      when: string;
    }>(sql`
      with picked as (
        (select e.id, e.date_from, e.kind, e.polity_id, 'recent' as when
          from election e join polity y on y.id = e.polity_id
          where e.parent_id is null and y.kind in ('sovereign_state', 'supranational')
            and e.kind in ('general', 'referendum') and e.date_from <= current_date
          order by e.date_from desc limit 6)
        union all
        (select e.id, e.date_from, e.kind, e.polity_id, 'upcoming'
          from election e
          where e.parent_id is null and e.date_from > current_date
          order by e.date_from limit 4)
        union all
        (select e.id, e.date_from, e.kind, e.polity_id, 'today'
          from election e
          where e.parent_id is null and e.date_from < current_date - 300
            and abs(extract(doy from e.date_from) - extract(doy from current_date)) <= 3
          order by (e.kind = 'general') desc, e.date_from desc limit 8)
      )
      select p.id, p.date_from::text as date, p.kind::text as kind, p.polity_id as polity, p.when, s.path,
        (select n.text from name n where n.entity_id = p.id and n.kind in ('official', 'english')
          order by n.is_preferred desc, (n.lang = 'en') desc, (n.kind = 'official') desc limit 1) as name
      from picked p join slug s on s.entity_id = p.id and s.is_current
    `),
  ]);
  const covered = polities.filter(
    (polity) =>
      polity.kind === "sovereign_state" || polity.kind === "supranational",
  );
  const names = new Map(polities.map((polity) => [polity.id, polity.name]));
  const elections = dated.rows.map((row) => ({
    name: row.name ?? "",
    path: row.path,
    date: row.date,
    kind: row.kind,
    polity: names.get(row.polity) ?? null,
    when: row.when,
  }));
  return {
    kind: "home" as const,
    polities: covered,
    atlas: shapes,
    recent: elections.filter((election) => election.when === "recent"),
    upcoming: elections
      .filter((election) => election.when === "upcoming")
      .toSorted((a, b) => a.date.localeCompare(b.date)),
    today: elections.filter((election) => election.when === "today"),
  };
}

export async function person(slug: string) {
  const { id } = await found(call(router.people.locate, { slug }));
  return {
    kind: "person" as const,
    id,
    person: await call(router.people.get, { id }),
  };
}

export async function party(path: string) {
  const segments = path.split("/");
  const { id } = await found(
    call(router.parties.locate, {
      polity: segments.length > 1 ? segments.slice(0, -1).join("/") : undefined,
      slug: segments.at(-1) ?? "",
    }),
  );
  const [party, record, history] = await Promise.all([
    call(router.parties.get, { id }),
    call(router.parties.elections, { id }),
    call(router.parties.history, { id }),
  ]);
  return { kind: "party" as const, id, party, record, history };
}

export async function archive(path: string, search: URLSearchParams) {
  const segments = path.split("/");
  const result = await db.execute<{
    path: string;
    entity_id: string;
    type: string;
    is_current: boolean;
    current: string | null;
  }>(sql`
    select s.path, s.entity_id, e.type::text as type, s.is_current,
      (select c.path from slug c where c.entity_id = s.entity_id and c.is_current) as current
    from slug s join entity e on e.id = s.entity_id
    where s.path = any(${sql.param(segments.map((_, index) => segments.slice(0, index + 1).join("/")))}::text[])
    order by length(s.path) desc
    limit 1
  `);
  const slug = result.rows[0];
  if (slug == null || (slug.path !== path && slug.type !== "contest")) {
    error(404);
  }
  const unit = path.slice(slug.path.length + 1);
  if (!slug.is_current && slug.current != null) {
    const query = search.toString();
    redirect(
      308,
      `/${[slug.current, unit].filter((part) => part !== "").join("/")}${query === "" ? "" : `?${query}`}`,
    );
  }
  const requested = search.get("match");
  const match =
    requested === "person" || requested === "family" ? requested : "party";
  if (slug.type === "contest") {
    const [contest, lists, rounds, ballot] = await found(
      Promise.all([
        call(router.contests.get, {
          id: slug.entity_id,
          unit,
          by: search.get("by") ?? undefined,
          vs: search.get("vs") ?? undefined,
          match,
          group: search.get("group") ?? undefined,
        }),
        call(router.contests.lists, { id: slug.entity_id }),
        call(router.contests.rounds, { id: slug.entity_id, unit }),
        call(router.contests.ballot, { id: slug.entity_id, unit }),
      ]),
    );
    const boundaries = await call(router.contests.boundaries, {
      id: slug.entity_id,
      unit,
      level: contest.places?.level,
    });
    return {
      kind: "contest" as const,
      contest,
      lists,
      rounds,
      ballot,
      boundaries,
      match,
    };
  }
  if (slug.type === "election") {
    return {
      kind: "election" as const,
      id: slug.entity_id,
      election: await call(router.elections.get, { id: slug.entity_id }),
    };
  }
  if (slug.type === "polity") {
    return {
      kind: "polity" as const,
      id: slug.entity_id,
      polity: await call(router.polities.get, { id: slug.entity_id }),
    };
  }
  error(404);
}
