import { releases } from "#lib/server/downloads.js";
import { TEXT_PAGES } from "#lib/text-pages.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ url }) => {
  const found = await releases();
  const pages = [
    ...Object.entries(TEXT_PAGES).map(([path, { modified }]) => ({
      path,
      modified,
    })),
    { path: "/downloads", modified: found[0]?.created_at.slice(0, 10) ?? null },
    ...found.map((row) => ({
      path: `/downloads/${row.calver}`,
      modified: row.created_at.slice(0, 10),
    })),
  ];
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n<url><loc>${url.origin}/</loc></url>\n${pages
      .map(
        ({ path, modified }) =>
          `<url><loc>${url.origin}${path}</loc>${modified == null ? "" : `<lastmod>${modified}</lastmod>`}</url>`,
      )
      .join("\n")}\n</urlset>\n`,
    { headers: { "content-type": "application/xml; charset=utf-8" } },
  );
};
