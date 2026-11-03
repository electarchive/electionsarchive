import { TEXT_PAGES } from "#lib/text-pages.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = ({ url }) =>
  new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n<url><loc>${url.origin}/</loc></url>\n${Object.entries(
      TEXT_PAGES,
    )
      .map(
        ([path, { modified }]) =>
          `<url><loc>${url.origin}${path}</loc><lastmod>${modified}</lastmod></url>`,
      )
      .join("\n")}\n</urlset>\n`,
    { headers: { "content-type": "application/xml; charset=utf-8" } },
  );
