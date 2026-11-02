import { call } from "@orpc/server";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const found = await call(router.polities.list, {
    kinds: ["sovereign_state", "supranational"],
    limit: 500,
  });
  const polities = found.polities
    .flatMap((polity) =>
      polity.path == null || polity.latest?.path == null
        ? []
        : [
            {
              name: polity.name,
              path: polity.path,
              elections: polity.elections,
              latest: { name: polity.latest.name, path: polity.latest.path },
            },
          ],
    )
    .toSorted((a, b) => a.name.localeCompare(b.name));
  const ids = await db.execute<{ path: string; id: string }>(sql`
    select path, entity_id::text as id from slug
    where path = any(${sql.param(polities.map((polity) => polity.latest.path))}::text[])
  `);
  const known = new Map(ids.rows.map((row) => [row.path, row.id]));
  return {
    polities,
    leads: Promise.all(
      polities.map(async (polity) => {
        const id = known.get(polity.latest.path);
        return id == null
          ? null
          : call(router.elections.lead, { id }).catch(() => null);
      }),
    ),
  };
};
