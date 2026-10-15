import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const segments = params.path.split("/");
  const found = await db.execute<{
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
  const slug = found.rows[0];
  if (slug == null || (slug.path !== params.path && slug.type !== "contest")) {
    error(404);
  }
  const unit = params.path.slice(slug.path.length + 1);
  if (!slug.is_current && slug.current != null) {
    redirect(
      308,
      `/${[slug.current, unit].filter((part) => part !== "").join("/")}${url.search}`,
    );
  }
  const requested = url.searchParams.get("match");
  const match =
    requested === "person" || requested === "family" ? requested : "party";
  if (slug.type === "contest") {
    try {
      return {
        kind: "contest" as const,
        contest: await call(router.contests.get, {
          id: slug.entity_id,
          unit,
          by: url.searchParams.get("by") ?? undefined,
          vs: url.searchParams.get("vs") ?? undefined,
          match,
        }),
        match,
      };
    } catch (error_) {
      if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
        error(404);
      }
      throw error_;
    }
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
};
