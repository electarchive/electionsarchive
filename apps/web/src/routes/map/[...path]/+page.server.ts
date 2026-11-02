import { redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { shell } from "#lib/atlas/data.js";
import { db } from "#lib/server/db/index.js";
import { atlasPage } from "#lib/server/pages.js";
import { races } from "#lib/server/rpc/archive.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const vs = url.searchParams.get("vs");
  if (vs?.includes("/") === true) {
    const found = await db.execute<{ id: string }>(sql`
      select s.entity_id::text as id from slug s join entity e on e.id = s.entity_id
      where s.path = ${vs} and e.type = 'contest' limit 1
    `);
    const next = new URL(url);
    next.searchParams.set("vs", found.rows[0]?.id ?? "previous");
    redirect(307, `${next.pathname}${next.search}`);
  }
  const held = await db.execute<{
    id: string;
    polity: string;
    date: string | null;
  }>(sql`
    select e.id::text as id, e.polity_id::text as polity, e.date_from::text as date
    from slug s join election e on e.id = s.entity_id
    where s.path = ${params.path} and s.is_current limit 1
  `);
  const election = held.rows[0];
  if (election != null) {
    const [race] = await races(
      election.id,
      election.polity,
      election.date,
      true,
    );
    const lead =
      race?.path ??
      (race != null && race.count >= 3
        ? `${params.path}/${race.anchor}`
        : null);
    redirect(
      307,
      lead == null ? `/${params.path}` : `/map/${lead}${url.search}`,
    );
  }
  const search = new URLSearchParams(url.searchParams);
  search.delete("level");
  const { contest, whole, path, atlas } = await atlasPage(params.path, search);
  return { contest, whole, path, shell: shell(atlas), query: url.search };
};
