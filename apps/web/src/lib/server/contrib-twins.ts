import { error } from "@sveltejs/kit";
import type { FeedEntry, HistoryEntry, ProposalState } from "#lib/contrib.js";
import type { Entry } from "#lib/server/atom.js";
import type { Bounty } from "#lib/server/bounties.js";
import type { change } from "#lib/server/history.js";
import type { view } from "#lib/server/rpc/proposals.js";
import {
  byline,
  describe,
  historyByline,
  roleLabel,
  stateLabel,
  tierLabel,
} from "#lib/contrib.js";
import { CATEGORY_LABELS, GRAIN_LABELS, levelLabel } from "#lib/coverage.js";
import { date } from "#lib/format.js";
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
    const { text, href } = describe(entry);
    const link = absolute(origin, href);
    const label = link == null ? inline(text) : `[${inline(text)}](${link})`;
    const extra = [
      byline(entry),
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

export function proposalsMarkdown(
  origin: string,
  heading: string,
  proposals: Array<{
    number: number;
    title: string;
    state: ProposalState;
    tier: number | null;
    author: string | null;
    polity: string | null;
    submitted_at: string;
    changes: number;
  }>,
  next: string | null,
) {
  const lines = [
    `# ${inline(heading)}`,
    "",
    "| # | Title | State | Tier | Author | Polity | Submitted | Changes |",
    "| ---: | --- | --- | --- | --- | --- | --- | ---: |",
    ...proposals.map((item) =>
      [
        "",
        item.number,
        `[${inline(item.title)}](${origin}/proposals/${item.number})`,
        stateLabel[item.state](),
        item.tier == null ? "" : (tierLabel[item.tier]?.() ?? item.tier),
        inline(item.author ?? ""),
        inline(item.polity ?? ""),
        item.submitted_at.slice(0, 10),
        item.changes,
        "",
      ]
        .join(" | ")
        .trim(),
    ),
  ];
  if (next != null) {
    lines.push("", `Older proposals: \`before=${next}\``);
  }
  return `${lines.join("\n")}\n`;
}

export function proposalMarkdown(
  origin: string,
  found: Awaited<ReturnType<typeof view>>,
) {
  const head = found.revisions[0];
  const payload = head?.payload ?? {};
  const rationale =
    typeof payload.rationale === "string" ? payload.rationale : null;
  const ops = Array.isArray(payload.ops) ? payload.ops : [];
  const lines = [
    `# Proposal ${found.number}: ${inline(found.title)}`,
    "",
    `- State: ${stateLabel[found.state]()}`,
    ...(found.tier == null
      ? []
      : [`- Tier: ${tierLabel[found.tier]?.() ?? found.tier}`]),
    `- Author: ${inline(found.author ?? "")}${found.agent_name == null ? "" : ` (agent: ${inline(found.agent_name)}${found.agent_model == null ? "" : `, ${inline(found.agent_model)}`})`}`,
    ...(found.submitted_at == null
      ? []
      : [`- Submitted: ${found.submitted_at.slice(0, 10)}`]),
    ...(found.decided_at == null
      ? []
      : [`- Decided: ${found.decided_at.slice(0, 10)}`]),
    `- Page: ${origin}/proposals/${found.number}`,
  ];
  if (found.decision_reason != null) {
    lines.push("", "## Decision", "", found.decision_reason);
  }
  if (rationale != null) {
    lines.push("", "## Rationale", "", rationale);
  }
  if (ops.length > 0) {
    lines.push(
      "",
      `## Changes (revision ${head?.n ?? 1})`,
      "",
      "```json",
      JSON.stringify(ops, null, 2),
      "```",
    );
  }
  const rows = head?.diff?.rows ?? [];
  if (rows.length > 0) {
    lines.push(
      "",
      "## Diff",
      "",
      "| Table | Op | Fields |",
      "| --- | --- | --- |",
      ...rows.slice(0, 500).map(
        (row) =>
          `| ${row.table} | ${row.op} | ${inline(
            Object.entries(row.fields)
              .map(
                ([field, [before, after]]) =>
                  `${field}: ${JSON.stringify(before)} → ${JSON.stringify(after)}`,
              )
              .join("; "),
          )} |`,
      ),
    );
    if (rows.length > 500 || head?.diff?.truncated === true) {
      lines.push("", "The diff continues in the JSON twin.");
    }
  }
  const comments = found.comments.filter((comment) => comment.body != null);
  if (comments.length > 0) {
    lines.push("", "## Discussion");
    for (const comment of comments) {
      lines.push(
        "",
        `### ${inline(comment.author ?? "")}, ${comment.created_at.slice(0, 10)}`,
        "",
        comment.body ?? "",
      );
    }
  }
  return `${lines.join("\n")}\n`;
}

const historyExtra = (entry: HistoryEntry) =>
  [
    roleLabel[entry.role](),
    historyByline(entry),
    entry.number == null ? null : m.history_proposal({ number: entry.number }),
    entry.state == null || entry.state === "published"
      ? null
      : stateLabel[entry.state](),
  ].filter((part) => part != null);

export function historyMarkdown(
  origin: string,
  entity: { public_id: string; name: string },
  entries: Array<HistoryEntry>,
  next: string | null,
) {
  const lines = [
    `# ${inline(m.history_page_title({ name: entity.name }))}`,
    "",
    m.history_lede(),
  ];
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
      `- ${entry.at.slice(11, 16)} UTC: [${inline(entry.title ?? entry.summary)}](${origin}/history/${entity.public_id}/${entry.changeset}) (${inline(historyExtra(entry).join("; "))})`,
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
    title: `${roleLabel[entry.role]()}: ${entry.title ?? entry.summary}`,
    updated: entry.at,
    author: historyByline(entry),
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
    `# ${inline(entry.title ?? entry.summary)}`,
    "",
    `- Record: [${inline(entity.name)}](${origin}/history/${entity.public_id})`,
    `- ${m.history_published({ date: entry.at.slice(0, 10), time: entry.at.slice(11, 16) })}`,
    `- ${inline(historyExtra(entry).join("; "))}`,
    ...(entry.number == null
      ? []
      : [`- Proposal: ${origin}/proposals/${entry.number}`]),
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

const bountyStatus = (bounty: Bounty) =>
  bounty.status === "fulfilled" && bounty.proposal != null
    ? m.bounty_filled({
        name: bounty.filler ?? m.feed_someone(),
        proposal: `#${bounty.proposal}`,
        date: date(bounty.closed_at),
      })
    : bounty.status === "closed"
      ? m.bounty_closed({ date: date(bounty.closed_at) })
      : bounty.claimer == null
        ? m.bounty_unclaimed()
        : m.bounty_claimed({
            name: bounty.claimer,
            date: date(bounty.claimed_until),
          });

export function bountiesMarkdown(
  origin: string,
  bounties: Array<Bounty>,
  next: number | null,
) {
  const lines = [
    `# ${inline(m.bounties_heading())}`,
    "",
    m.bounties_lede(),
    "",
    "| # | Bounty | Place | Years | Missing | Category | Status |",
    "| ---: | --- | --- | --- | --- | --- | --- |",
    ...bounties.map((bounty) =>
      [
        "",
        bounty.number,
        `[${inline(bounty.label)}](${origin}/bounties/${bounty.number})`,
        inline(bounty.place ?? bounty.polity.toUpperCase()),
        bounty.years,
        GRAIN_LABELS[bounty.grain]?.() ?? bounty.grain,
        CATEGORY_LABELS[bounty.category](),
        inline(bountyStatus(bounty)),
        "",
      ]
        .join(" | ")
        .trim(),
    ),
  ];
  if (next != null) {
    lines.push("", `More bounties: \`after=${next}\``);
  }
  return `${lines.join("\n")}\n`;
}

export function bountyMarkdown(origin: string, bounty: Bounty) {
  const facts = [
    [m.bounty_place(), bounty.place ?? bounty.polity.toUpperCase()],
    [m.bounty_missing(), GRAIN_LABELS[bounty.grain]?.() ?? bounty.grain],
    [m.bounty_years(), bounty.years],
    [
      m.bounty_level(),
      bounty.level === "" ? "" : levelLabel(bounty.polity, bounty.level),
    ],
    [m.bounty_stage(), bounty.stage],
    [m.bounty_category(), CATEGORY_LABELS[bounty.category]()],
    [m.bounty_checked(), bounty.checked ?? ""],
    [m.bounty_state(), bountyStatus(bounty)],
  ].filter(([, value]) => value !== "");
  return `${[
    `# ${inline(bounty.label)}`,
    "",
    `${origin}/bounties/${bounty.number}`,
    "",
    bounty.reason,
    "",
    ...facts.map(([term, value]) => `- ${term}: ${inline(value ?? "")}`),
    ...(bounty.sources === ""
      ? []
      : ["", `## ${m.bounty_sources()}`, "", bounty.sources]),
  ].join("\n")}\n`;
}
