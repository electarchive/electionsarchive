import { home } from "#lib/server/pages.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ url }) => {
  const { polities } = await home();
  let total = 0;
  for (const polity of polities) {
    total += polity.elections + polity.within;
  }
  const origin = url.origin;
  return new Response(
    `# Elections Archive

> Election results down to the smallest reporting unit, from each country's first election to its latest. Every number traces to a published source. The archive holds ${new Intl.NumberFormat("en").format(total)} elections so far.

Every page is also available as Markdown or JSON. Add \`.md\` or \`.json\` to any page URL, or send an \`Accept: text/markdown\` or \`Accept: application/json\` header. Links inside the Markdown point to other Markdown pages, so you can browse the archive without leaving Markdown. The home page is at ${origin}/index.md.

Pages follow the place, then the election, then the contest, then the reporting unit: ${origin}/us/ga/2020-11-03-general/senate/fulton-county-ga. People are at /people/<slug>, parties at /parties/<polity>/<slug>, and /id/<uuid> redirects any record to its page.

## API

- [REST API](${origin}/api/v1/openapi.json): OpenAPI description of the read API, including search.

## Countries

${polities
  .filter(
    (polity) => polity.path != null && polity.elections + polity.within > 0,
  )
  .map((polity) => `- [${polity.name}](${origin}/${polity.path}.md)`)
  .join("\n")}
`,
    { headers: { "content-type": "text/plain; charset=utf-8" } },
  );
};
