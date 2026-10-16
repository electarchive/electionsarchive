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

export async function home() {
  const polities = await call(router.polities.list, undefined);
  return {
    kind: "home" as const,
    polities: polities.filter(
      (polity) =>
        polity.kind === "sovereign_state" || polity.kind === "supranational",
    ),
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
      election: await call(router.elections.get, { id: slug.entity_id }),
    };
  }
  if (slug.type === "polity") {
    return {
      kind: "polity" as const,
      polity: await call(router.polities.get, { id: slug.entity_id }),
    };
  }
  error(404);
}
