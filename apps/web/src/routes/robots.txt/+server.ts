import { TRAINING } from "#lib/server/crawlers.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = ({ url }) =>
  new Response(
    `User-agent: *
Content-Signal: search=yes, ai-input=yes, ai-train=yes
Allow: /

# Crawlers that collect text for AI training are welcome. Every page they request is answered with its Markdown twin, the same as adding .md to the URL. ${url.origin}/llms.txt describes the twins.
${TRAINING.map((name) => `User-agent: ${name}`).join("\n")}
Content-Signal: search=yes, ai-input=yes, ai-train=yes
Allow: /

User-agent: ShapBot
User-agent: Applebot
Content-Signal: search=yes, ai-input=yes, ai-train=yes
Crawl-delay: 3
Allow: /

User-agent: Epochal-Search
Disallow: /

Sitemap: ${url.origin}/sitemap.xml
`,
    { headers: { "content-type": "text/plain; charset=utf-8" } },
  );
