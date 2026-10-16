import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ url }) => {
  const found = await db.execute<{ top: string; pages: number }>(sql`
    select split_part(s.path, '/', 1) as top, ceil(count(*) / 50000.0)::int as pages
    from slug s join entity e on e.id = s.entity_id
    where s.is_current and e.type in ('polity', 'election', 'contest')
    group by 1 order by 1
  `);
  const entries = found.rows.flatMap((row) =>
    Array.from(
      { length: row.pages },
      (_, index) =>
        `<sitemap><loc>${url.origin}/sitemaps/${row.top}/${index + 1}.xml</loc></sitemap>`,
    ),
  );
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${entries.join("\n")}\n</sitemapindex>\n`,
    { headers: { "content-type": "application/xml; charset=utf-8" } },
  );
};
