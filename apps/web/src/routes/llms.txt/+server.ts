import { READ_ONLY } from "$app/env/public";
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

Pages follow the place, then the election, then the contest, then the reporting unit: ${origin}/us/ga/2020-11-03-general/senate/fulton-county-ga. People are at /people/<slug>, parties at /parties/<polity>/<slug> (add \`?in=<polity path>\` for one state's or region's branch), chambers at /chambers/<polity>/<slug>, and /id/<uuid> redirects any record to its page. Add \`?vs=previous\`, or \`?vs=<election path or uuid>\`, to a contest page to compare it with an earlier election: the twins then carry the earlier share and the change in percentage points for every candidate and place. /compare?a=<uuid>&b=<uuid> sets two people, two places, or two parties side by side.

## API

- [REST API](${origin}/api/v1/openapi.json): OpenAPI description of the REST API, for reading${READ_ONLY ? " and searching" : ", searching, and proposing changes"}.
- [MCP server](${origin}/mcp): the same tools over MCP. ${READ_ONLY ? "Reading needs no key. Contributions open after launch." : "Reading needs no key; proposing changes needs an API key, sent in the x-api-key header."}
- [Skill](${origin}/agents/skill.md): ${READ_ONLY ? "how to connect, and the data model in one page." : "how to propose a change: the data model, the evidence rule, the workflow, and every operation, with worked examples."}
- [For agents](${origin}/agents): setup, tools, and rules for people connecting an agent.

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
