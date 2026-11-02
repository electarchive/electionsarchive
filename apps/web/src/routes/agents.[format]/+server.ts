import { READ_ONLY } from "$app/env/public";
import { connect, INSTALL } from "#lib/agents.js";
import { m } from "#lib/paraglide/messages.js";
import { formatOf, twinResponse } from "#lib/server/contrib-twins.js";
import { tools } from "#lib/server/mcp.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = ({ params, url }) => {
  const format = formatOf(params.format, ["md", "json"]);
  const setup = connect(url.origin);
  const lede = READ_ONLY ? m.agents_lede_read_only() : m.agents_lede();
  const listed = READ_ONLY ? tools.filter((tool) => !tool.isKeyed) : tools;
  const links = {
    skill: `${url.origin}/agents/skill.md`,
    openapi: `${url.origin}/api/v1/openapi.json`,
    rest: `${url.origin}/api/v1`,
    llms: `${url.origin}/llms.txt`,
  };
  return twinResponse({
    format,
    origin: url.origin,
    page: "/agents",
    title: m.agents_title(),
    subtitle: lede,
    markdown: () => {
      const lines = [
        `# ${m.agents_title()}`,
        "",
        lede,
        "",
        `## ${m.agents_skill_title()}`,
        "",
        m.agents_skill_body(),
        "",
        "```sh",
        INSTALL,
        "```",
        "",
        `[skill.md](${links.skill})`,
        "",
        `## ${m.agents_server_title()}`,
        "",
        `\`${setup.mcp}\`. ${m.agents_server_body()}`,
      ];
      for (const client of setup.clients) {
        lines.push("", `### ${client.label}`, "");
        if (client.body != null) {
          lines.push(client.body, "");
        }
        lines.push(
          client.value === "other"
            ? "```json"
            : client.value === "claude-app"
              ? "```text"
              : "```sh",
          client.code,
          "```",
        );
      }
      lines.push("", `## ${m.agents_ask_title()}`, "");
      for (const question of setup.questions) {
        lines.push(`- ${question}`);
      }
      lines.push(
        "",
        `## ${m.agents_tools_count({ count: listed.length })}`,
        "",
      );
      for (const tool of listed) {
        lines.push(`- \`${tool.name}\`: ${tool.description}`);
      }
      lines.push(
        "",
        `## ${m.agents_more_title()}`,
        "",
        `- ${m.agents_rest()}: ${links.rest} ([OpenAPI](${links.openapi}))`,
        `- llms.txt: ${links.llms}`,
      );
      return `${lines.join("\n")}\n`;
    },
    json: () => ({
      mcp: setup.mcp,
      transport: "streamable-http",
      isKeyRequired: false,
      skill: { install: INSTALL, url: links.skill },
      clients: setup.clients.map(({ label, code, body }) => ({
        name: label,
        setup: code,
        note: body,
      })),
      questions: setup.questions,
      tools: listed.map(({ name, description }) => ({ name, description })),
      openapi: links.openapi,
      rest: links.rest,
      llms: links.llms,
    }),
  });
};
