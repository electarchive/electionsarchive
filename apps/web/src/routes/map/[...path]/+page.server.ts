import { call, ORPCError } from "@orpc/server";
import { error, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { representative } from "#lib/server/rpc/archive.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const segments = params.path.split("/");
  let slug:
    | {
        path: string;
        entity_id: string;
        is_current: boolean;
        current: string | null;
      }
    | undefined;
  let whole: string | null = null;
  for (const type of ["contest", "election"]) {
    const result = await db.execute<{
      path: string;
      entity_id: string;
      is_current: boolean;
      current: string | null;
    }>(sql`
      select s.path, s.entity_id, s.is_current,
        (select c.path from slug c where c.entity_id = s.entity_id and c.is_current) as current
      from slug s join entity e on e.id = s.entity_id
      where e.type = ${type}
        and s.path = any(${sql.param(segments.map((_, index) => segments.slice(0, index + 1).join("/")))}::text[])
      order by length(s.path) desc
      limit 1
    `);
    const held = result.rows[0];
    if (held == null) {
      continue;
    }
    if (type === "contest") {
      slug = held;
      break;
    }
    const anchor = params.path.slice(held.path.length + 1).split("/")[0] ?? "";
    const id =
      anchor === "" ? undefined : await representative(held.entity_id, anchor);
    if (id != null) {
      slug = {
        path: `${held.path}/${anchor}`,
        entity_id: id,
        is_current: held.is_current,
        current: held.current == null ? null : `${held.current}/${anchor}`,
      };
      whole = held.entity_id;
    }
  }
  if (slug == null) {
    error(404);
  }
  const unit = params.path.slice(slug.path.length + 1);
  if (!slug.is_current && slug.current != null) {
    redirect(
      308,
      `/map/${[slug.current, unit].filter((part) => part !== "").join("/")}${url.search}`,
    );
  }
  try {
    return {
      contest: slug.entity_id,
      whole,
      path: slug.is_current ? slug.path : (slug.current ?? slug.path),
      atlas: await call(router.contests.atlas, {
        id: slug.entity_id,
        unit: unit === "" ? undefined : unit,
        level: url.searchParams.get("level") ?? undefined,
        vs: url.searchParams.get("vs") ?? undefined,
        group: url.searchParams.get("group") ?? undefined,
        whole: whole ?? undefined,
      }),
    };
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
};
