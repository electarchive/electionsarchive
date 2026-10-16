import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const page = Number(params.page);
  if (!Number.isSafeInteger(page) || page < 1) {
    error(404);
  }
  const found = await db.execute<{ path: string }>(sql`
    select s.path
    from slug s join entity e on e.id = s.entity_id
    where s.is_current and e.type in ('polity', 'election', 'contest')
      and (s.path = ${params.polity} or s.path like ${`${params.polity}/%`})
    order by s.path
    limit 50000 offset ${(page - 1) * 50_000}
  `);
  if (found.rows.length === 0) {
    error(404);
  }
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${found.rows
      .map(
        (row) =>
          `<url><loc>${url.origin}/${row.path.replaceAll("&", "&amp;")}</loc></url>`,
      )
      .join("\n")}\n</urlset>\n`,
    { headers: { "content-type": "application/xml; charset=utf-8" } },
  );
};
