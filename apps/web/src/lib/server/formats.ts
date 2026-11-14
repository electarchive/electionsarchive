import { error } from "@sveltejs/kit";
import type { FeedEntry, HistoryEntry } from "#lib/changes.js";
import type { Entry } from "#lib/server/atom.js";
import type { change } from "#lib/server/history.js";
import { importedBy, roleLabel } from "#lib/changes.js";
import { m } from "#lib/paraglide/messages.js";
import { atom } from "#lib/server/atom.js";

export const FORMATS = ["atom", "md", "json"] as const;

type Format = (typeof FORMATS)[number];

const TYPES: Record<Exclude<Format, "atom">, string> = {
  md: "text/markdown; charset=utf-8",
  json: "application/json; charset=utf-8",
};

export function formatOf(value: string, allowed: ReadonlyArray<Format>) {
  const found = allowed.find((format) => format === value);
  if (found == null) {
    error(404);
  }
  return found;
}

const inline = (value: string) =>
  value.replaceAll(/[\\`*_[\]<>|]/gu, (character) => `\\${character}`);

const absolute = (origin: string, path: string | null) =>
  path == null ? null : `${origin}${path}`;

export function twinResponse({
  format,
  origin,
  page,
  title,
  subtitle,
  markdown,
  json,
  entries,
}: {
  format: Format;
  origin: string;
  page: string;
  title: string;
  subtitle: string;
  markdown: () => string;
  json: () => unknown;
  entries?: () => Array<Entry>;
}) {
  if (format === "atom") {
    if (entries == null) {
      error(404);
    }
    const query = page.includes("?") ? page.slice(page.indexOf("?")) : "";
    const path = page.replace(/\?.*$/u, "");
    return atom({
      origin,
      self: `${path}.atom${query}`,
      page,
      title,
      subtitle,
      entries: entries(),
      fallback: new Date().toISOString(),
    });
  }
  const headers = {
    "content-type": TYPES[format],
    "cache-control": "no-store",
    link: `<${origin}${page}>; rel="canonical"`,
  };
  return format === "json"
    ? Response.json(json(), { headers })
    : new Response(markdown(), { headers });
}

export function feedMarkdown(
  origin: string,
  heading: string,
  lede: string,
  entries: Array<FeedEntry>,
  next: string | null,
) {
  const lines = [`# ${inline(heading)}`, "", lede, ""];
  let day = "";
  for (const entry of entries) {
    if (entry.at.slice(0, 10) !== day) {
      day = entry.at.slice(0, 10);
      lines.push("", `## ${day}`, "");
    }
    const link = absolute(
      origin,
      entry.polity_path == null ? null : `/${entry.polity_path}`,
    );
    const label =
      link == null ? inline(entry.title) : `[${inline(entry.title)}](${link})`;
    const extra = [
      importedBy(entry.pipeline),
      entry.polity,
      entry.batches > 1 ? m.feed_batches({ count: entry.batches }) : null,
    ].filter((part) => part != null);
    lines.push(
      `- ${entry.at.slice(11, 16)} UTC: ${label} (${inline(extra.join("; "))})`,
    );
  }
  if (next != null) {
    lines.push("", `Older entries: \`before=${next}\``);
  }
  return `${lines.join("\n").replaceAll(/\n{3,}/gu, "\n\n")}\n`;
}

const historyExtra = (entry: HistoryEntry) => [
  roleLabel[entry.role](),
  importedBy(entry.pipeline),
];

export function historyMarkdown(
  origin: string,
  entity: { public_id: string; name: string },
  entries: Array<HistoryEntry>,
  next: string | null,
) {
  const lines = [`# ${inline(m.history_page_title({ name: entity.name }))}`];
  if (entries.length === 0) {
    lines.push("", m.history_empty());
  }
  let day = "";
  for (const entry of entries) {
    if (entry.at.slice(0, 10) !== day) {
      day = entry.at.slice(0, 10);
      lines.push("", `## ${day}`, "");
    }
    lines.push(
      `- ${entry.at.slice(11, 16)} UTC: [${inline(entry.summary)}](${origin}/history/${entity.public_id}/${entry.changeset}) (${inline(historyExtra(entry).join("; "))})`,
    );
  }
  if (next != null) {
    lines.push("", `Older entries: \`before=${next}\``);
  }
  return `${lines.join("\n")}\n`;
}

export const historyEntries = (
  publicId: string,
  entries: Array<HistoryEntry>,
): Array<Entry> =>
  entries.map((entry) => ({
    id: entry.changeset,
    title: `${roleLabel[entry.role]()}: ${entry.summary}`,
    updated: entry.at,
    author: importedBy(entry.pipeline),
    path: `/history/${publicId}/${entry.changeset}`,
  }));

export function changeMarkdown(
  origin: string,
  found: Awaited<ReturnType<typeof change>>,
) {
  const { entity, entry, changes, names } = found;
  const shown = (value: unknown) =>
    value == null
      ? m.history_empty_value()
      : typeof value === "string"
        ? (names[value] ?? value)
        : JSON.stringify(value);
  const lines = [
    `# ${inline(entry.summary)}`,
    "",
    `- Record: [${inline(entity.name)}](${origin}/history/${entity.public_id})`,
    `- ${m.history_published({ date: entry.at.slice(0, 10), time: entry.at.slice(11, 16) })}`,
    `- ${inline(historyExtra(entry).join("; "))}`,
    "",
    `## ${m.history_fields()}`,
    "",
  ];
  if (changes.length === 0) {
    lines.push(m.history_no_fields());
  } else {
    lines.push(
      "| Table | Op | Field | Before | After |",
      "| --- | --- | --- | --- | --- |",
    );
    for (const item of changes) {
      for (const field of item.fields) {
        lines.push(
          `| ${item.table} | ${item.op} | ${field.field} | ${item.op === "add" ? "" : inline(shown(field.before))} | ${item.op === "drop" ? "" : inline(shown(field.after))} |`,
        );
      }
    }
  }
  return `${lines.join("\n")}\n`;
}
