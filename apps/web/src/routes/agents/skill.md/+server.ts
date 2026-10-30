import { GUIDE } from "#lib/server/rpc/agents.js";
import skill from "#lib/server/skill.md?raw";
import type { RequestHandler } from "./$types";

function reference() {
  const lines: Array<string> = ["### Ops", ""];
  for (const [name, op] of Object.entries(GUIDE.ops)) {
    lines.push(`- **${name}**: ${op.note} \`${JSON.stringify(op.shape)}\``);
  }
  for (const [name, table] of Object.entries(GUIDE.tables)) {
    lines.push(
      "",
      `### ${name}`,
      "",
      `Ops: ${table.ops.join(", ")}. Tier: ${table.tier}. Evidence: ${table.evidence}.`,
    );
    if (table.key != null) {
      lines.push(`Keyed by ${table.key.join(" and ")}.`);
    }
    const columns = Object.entries(table.columns);
    if (columns.length === 0) {
      continue;
    }
    lines.push("", "| Column | Type | Required |", "| --- | --- | --- |");
    for (const [column, spec] of columns) {
      const type =
        spec.values == null ? spec.type : `one of ${spec.values.join(", ")}`;
      lines.push(
        `| \`${column}\` | ${type} | ${spec.required ? "yes" : "no"} |`,
      );
    }
  }
  lines.push(
    "",
    "### Drop only",
    "",
    `Rows of ${GUIDE.drop_only.join(", ")} can be dropped by id or key, with a reason and evidence.`,
    "",
    GUIDE.not_yet,
  );
  return lines.join("\n");
}

export const GET: RequestHandler = ({ url }) =>
  new Response(
    skill
      .replaceAll("{{origin}}", () => url.origin)
      .replace("{{ops}}", () => reference()),
    {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "cache-control": "public, max-age=3600",
      },
    },
  );
